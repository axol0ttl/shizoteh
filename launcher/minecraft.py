"""
minecraft.py — Скачивание и запуск Minecraft с Fabric Loader.

Полный цикл:
1. Скачать client.jar и version.json с серверов Mojang
2. Скачать библиотеки и нативные файлы
3. Скачать ассеты (индекс + файлы)
4. Скачать Fabric Loader и его библиотеки
5. Собрать classpath и запустить Java-процесс с оффлайн-аккаунтом
"""

import hashlib
import json
import os
import platform
import subprocess
import time
import uuid
from typing import Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Mojang API URLs
VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
ASSETS_BASE_URL = "https://resources.download.minecraft.com"

# Fabric Meta API
FABRIC_META_URL = "https://meta.fabricmc.net/v2/versions/loader/{mc_version}/{loader_version}/profile/json"

# Количество попыток скачивания и задержка между ними
MAX_RETRIES = 5
RETRY_BACKOFF = 1.5  # секунды (экспоненциально: 1.5, 3, 6, 12, 24)


def _create_session() -> requests.Session:
    """Создаёт requests.Session с автоматическим retry на сетевые ошибки."""
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# Глобальная сессия с retry
_session = _create_session()


def get_os_name() -> str:
    """Возвращает имя ОС в формате Mojang."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    elif system == "Darwin":
        return "osx"
    else:
        return "linux"


def get_arch() -> str:
    """Возвращает архитектуру процессора."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    elif machine in ("aarch64", "arm64"):
        return "arm64"
    elif machine in ("i386", "i686", "x86"):
        return "x86"
    return machine


def get_classpath_separator() -> str:
    """Разделитель classpath: ';' для Windows, ':' для остальных."""
    return ";" if platform.system() == "Windows" else ":"


def generate_offline_uuid(username: str) -> str:
    """Генерирует оффлайн UUID по стандарту Minecraft."""
    data = f"OfflinePlayer:{username}".encode("utf-8")
    offline_uuid = uuid.uuid3(uuid.NAMESPACE_URL, data.hex())
    return str(offline_uuid).replace("-", "")


def _download_file(
    url: str,
    path: str,
    expected_sha1: str | None = None,
    progress_callback: Callable[[str, float], None] | None = None,
    label: str = ""
) -> None:
    """
    Скачивает файл с retry-логикой при сетевых ошибках.

    Args:
        url: URL для скачивания.
        path: Путь для сохранения.
        expected_sha1: Ожидаемый SHA1-хеш (опционально).
        progress_callback: Callback(label, progress_0_1).
        label: Метка для callback.
    """
    # Проверяем, существует ли файл с правильным хешем
    if os.path.exists(path) and expected_sha1:
        sha1 = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha1.update(chunk)
        if sha1.hexdigest() == expected_sha1:
            return  # Файл уже есть и хеш совпадает
    elif os.path.exists(path) and not expected_sha1:
        return  # Файл есть, проверка хеша не требуется

    os.makedirs(os.path.dirname(path), exist_ok=True)

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.get(url, stream=True, timeout=30)
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        progress_callback(label or os.path.basename(path), downloaded / total)
            return  # Успешно скачано

        except (requests.ConnectionError, requests.Timeout, OSError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * (2 ** (attempt - 1))
                time.sleep(wait)
            continue

    # Все попытки исчерпаны
    raise ConnectionError(
        f"Не удалось скачать {url} после {MAX_RETRIES} попыток: {last_error}"
    )


def _check_library_rules(library: dict) -> bool:
    """Проверяет, подходит ли библиотека для текущей ОС по правилам."""
    rules = library.get("rules")
    if rules is None:
        return True

    os_name = get_os_name()
    allowed = False

    for rule in rules:
        action = rule.get("action", "allow")
        rule_os = rule.get("os", {})

        if not rule_os:
            # Правило без условия ОС
            allowed = (action == "allow")
        elif rule_os.get("name") == os_name:
            allowed = (action == "allow")

    return allowed


def _get_library_path(name: str) -> str:
    """
    Конвертирует Maven-координаты (group:artifact:version) в путь к файлу.
    Пример: 'com.mojang:brigadier:1.0.18' -> 'com/mojang/brigadier/1.0.18/brigadier-1.0.18.jar'
    """
    parts = name.split(":")
    if len(parts) < 3:
        return name

    group, artifact, version = parts[0], parts[1], parts[2]
    group_path = group.replace(".", "/")

    # Поддержка суффиксов (например com.mojang:text2speech:1.13.9:natives-windows)
    if len(parts) > 3:
        classifier = parts[3]
        return f"{group_path}/{artifact}/{version}/{artifact}-{version}-{classifier}.jar"

    return f"{group_path}/{artifact}/{version}/{artifact}-{version}.jar"


def download_minecraft(
    game_dir: str,
    mc_version: str,
    progress_callback: Callable[[str, float], None] | None = None,
    status_callback: Callable[[str], None] | None = None
) -> dict:
    """
    Скачивает клиент Minecraft указанной версии.

    Args:
        game_dir: Корневая папка игры.
        mc_version: Версия Minecraft (например, "1.21.2").
        progress_callback: Callback для прогресса.
        status_callback: Callback для статуса.

    Returns:
        version_json — словарь с версией Minecraft.
    """
    versions_dir = os.path.join(game_dir, "versions", mc_version)
    os.makedirs(versions_dir, exist_ok=True)

    # --- 1. Получаем манифест версий ---
    if status_callback:
        status_callback("Загрузка манифеста версий...")

    resp = requests.get(VERSION_MANIFEST_URL, timeout=15)
    resp.raise_for_status()
    manifest = resp.json()

    # Ищем нужную версию
    version_entry = None
    for v in manifest["versions"]:
        if v["id"] == mc_version:
            version_entry = v
            break

    if not version_entry:
        raise ValueError(f"Версия {mc_version} не найдена в манифесте Mojang")

    # --- 2. Скачиваем version.json ---
    version_json_path = os.path.join(versions_dir, f"{mc_version}.json")
    if not os.path.exists(version_json_path):
        if status_callback:
            status_callback(f"Загрузка {mc_version}.json...")

        resp = requests.get(version_entry["url"], timeout=15)
        resp.raise_for_status()
        with open(version_json_path, "w", encoding="utf-8") as f:
            f.write(resp.text)
        version_json = resp.json()
    else:
        with open(version_json_path, "r", encoding="utf-8") as f:
            version_json = json.load(f)

    # --- 3. Скачиваем client.jar ---
    client_jar = os.path.join(versions_dir, f"{mc_version}.jar")
    client_dl = version_json["downloads"]["client"]
    if status_callback:
        status_callback("Загрузка client.jar...")

    _download_file(
        url=client_dl["url"],
        path=client_jar,
        expected_sha1=client_dl.get("sha1"),
        progress_callback=progress_callback,
        label="client.jar"
    )

    # --- 4. Скачиваем библиотеки ---
    libraries_dir = os.path.join(game_dir, "libraries")
    libs = version_json.get("libraries", [])
    lib_count = len(libs)

    for i, lib in enumerate(libs):
        if not _check_library_rules(lib):
            continue

        downloads = lib.get("downloads", {})

        # Основной артефакт
        artifact = downloads.get("artifact")
        if artifact:
            lib_path = os.path.join(libraries_dir, artifact["path"])
            if status_callback:
                status_callback(f"Библиотеки ({i+1}/{lib_count}): {os.path.basename(artifact['path'])}")
            _download_file(
                url=artifact["url"],
                path=lib_path,
                expected_sha1=artifact.get("sha1"),
                progress_callback=progress_callback,
                label=os.path.basename(artifact["path"])
            )

        # Нативные библиотеки
        classifiers = downloads.get("classifiers", {})
        natives = lib.get("natives", {})
        os_name = get_os_name()
        if os_name in natives:
            native_key = natives[os_name].replace("${arch}", get_arch())
            if native_key in classifiers:
                native_info = classifiers[native_key]
                native_path = os.path.join(libraries_dir, native_info["path"])
                _download_file(
                    url=native_info["url"],
                    path=native_path,
                    expected_sha1=native_info.get("sha1"),
                    progress_callback=progress_callback,
                    label=os.path.basename(native_info["path"])
                )

    # --- 5. Скачиваем ассеты ---
    asset_index_info = version_json.get("assetIndex", {})
    assets_dir = os.path.join(game_dir, "assets")
    indexes_dir = os.path.join(assets_dir, "indexes")
    objects_dir = os.path.join(assets_dir, "objects")
    os.makedirs(indexes_dir, exist_ok=True)
    os.makedirs(objects_dir, exist_ok=True)

    asset_index_id = asset_index_info.get("id", mc_version)
    asset_index_path = os.path.join(indexes_dir, f"{asset_index_id}.json")

    if status_callback:
        status_callback("Загрузка индекса ассетов...")

    _download_file(
        url=asset_index_info["url"],
        path=asset_index_path,
        expected_sha1=asset_index_info.get("sha1"),
        progress_callback=progress_callback,
        label="asset_index.json"
    )

    with open(asset_index_path, "r", encoding="utf-8") as f:
        asset_index = json.load(f)

    objects = asset_index.get("objects", {})
    total_assets = len(objects)

    for idx, (asset_name, asset_info) in enumerate(objects.items()):
        sha1 = asset_info["hash"]
        prefix = sha1[:2]
        asset_path = os.path.join(objects_dir, prefix, sha1)

        if idx % 100 == 0 and status_callback:
            status_callback(f"Ассеты ({idx}/{total_assets})...")

        _download_file(
            url=f"{ASSETS_BASE_URL}/{prefix}/{sha1}",
            path=asset_path,
            expected_sha1=sha1
        )

    if status_callback:
        status_callback("Minecraft загружен!")

    return version_json


def download_fabric(
    game_dir: str,
    mc_version: str,
    loader_version: str,
    status_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[str, float], None] | None = None
) -> dict:
    """
    Скачивает Fabric Loader и его библиотеки.

    Returns:
        fabric_json — профиль Fabric (включая библиотеки и mainClass).
    """
    if status_callback:
        status_callback("Загрузка профиля Fabric Loader...")

    fabric_url = FABRIC_META_URL.format(mc_version=mc_version, loader_version=loader_version)
    resp = requests.get(fabric_url, timeout=15)
    resp.raise_for_status()
    fabric_json = resp.json()

    # Сохраняем профиль
    fabric_version_id = fabric_json.get("id", f"fabric-loader-{loader_version}-{mc_version}")
    fabric_dir = os.path.join(game_dir, "versions", fabric_version_id)
    os.makedirs(fabric_dir, exist_ok=True)

    fabric_json_path = os.path.join(fabric_dir, f"{fabric_version_id}.json")
    with open(fabric_json_path, "w", encoding="utf-8") as f:
        json.dump(fabric_json, f, indent=2)

    # Скачиваем библиотеки Fabric
    libraries_dir = os.path.join(game_dir, "libraries")
    fabric_libs = fabric_json.get("libraries", [])

    for i, lib in enumerate(fabric_libs):
        lib_name = lib.get("name", "")
        lib_rel_path = _get_library_path(lib_name)
        lib_path = os.path.join(libraries_dir, lib_rel_path)

        # URL библиотеки
        lib_url = lib.get("url", "https://maven.fabricmc.net/")
        if not lib_url.endswith("/"):
            lib_url += "/"
        full_url = lib_url + lib_rel_path

        if status_callback:
            status_callback(f"Fabric библиотеки ({i+1}/{len(fabric_libs)}): {os.path.basename(lib_rel_path)}")

        _download_file(
            url=full_url,
            path=lib_path,
            progress_callback=progress_callback,
            label=os.path.basename(lib_rel_path)
        )

    if status_callback:
        status_callback("Fabric Loader установлен!")

    return fabric_json


def _build_classpath(game_dir: str, mc_version_json: dict, fabric_json: dict, mc_version: str) -> str:
    """Собирает полный classpath из библиотек MC + Fabric + client.jar."""
    libraries_dir = os.path.join(game_dir, "libraries")
    sep = get_classpath_separator()
    paths = []

    # Библиотеки Minecraft
    for lib in mc_version_json.get("libraries", []):
        if not _check_library_rules(lib):
            continue
        artifact = lib.get("downloads", {}).get("artifact")
        if artifact:
            lib_path = os.path.join(libraries_dir, artifact["path"])
            if os.path.exists(lib_path):
                paths.append(lib_path)

    # Библиотеки Fabric
    for lib in fabric_json.get("libraries", []):
        lib_name = lib.get("name", "")
        lib_rel_path = _get_library_path(lib_name)
        lib_path = os.path.join(libraries_dir, lib_rel_path)
        if os.path.exists(lib_path):
            paths.append(lib_path)

    # client.jar
    client_jar = os.path.join(game_dir, "versions", mc_version, f"{mc_version}.jar")
    paths.append(client_jar)

    return sep.join(paths)


def _substitute_args(args_template: list, replacements: dict) -> list[str]:
    """Подставляет значения переменных в аргументы запуска."""
    result = []
    for arg in args_template:
        if isinstance(arg, dict):
            # Сложный аргумент с правилами — упрощаем
            value = arg.get("value")
            if value:
                if isinstance(value, list):
                    for v in value:
                        for key, val in replacements.items():
                            v = v.replace(key, str(val))
                        result.append(v)
                else:
                    for key, val in replacements.items():
                        value = value.replace(key, str(val))
                    result.append(value)
        else:
            s = str(arg)
            for key, val in replacements.items():
                s = s.replace(key, str(val))
            result.append(s)
    return result


def launch_minecraft(
    game_dir: str,
    mc_version: str,
    mc_version_json: dict,
    fabric_json: dict,
    username: str,
    server_host: str,
    server_port: int,
    java_path: str = "java",
    min_ram_mb: int = 2048,
    max_ram_mb: int = 4096,
    extra_java_args: list[str] | None = None,
    status_callback: Callable[[str], None] | None = None
) -> subprocess.Popen:
    """
    Запускает Minecraft с Fabric Loader, сразу подключаясь к серверу.

    Args:
        game_dir: Корневая папка игры.
        mc_version: Версия Minecraft.
        mc_version_json: Содержимое version.json.
        fabric_json: Содержимое fabric profile JSON.
        username: Ник игрока.
        server_host: IP сервера для прямого подключения.
        server_port: Порт сервера.
        java_path: Путь к java.
        min_ram_mb: Мин. RAM (Xms).
        max_ram_mb: Макс. RAM (Xmx).
        extra_java_args: Дополнительные аргументы JVM.
        status_callback: Callback для статуса.

    Returns:
        subprocess.Popen — процесс Minecraft.
    """
    # Classpath
    classpath = _build_classpath(game_dir, mc_version_json, fabric_json, mc_version)

    # Main class — из Fabric-профиля
    main_class = fabric_json.get("mainClass", "net.minecraft.client.main.Main")

    # Генерируем оффлайн UUID
    player_uuid = generate_offline_uuid(username)

    # Нативные библиотеки
    natives_dir = os.path.join(game_dir, "natives", mc_version)
    os.makedirs(natives_dir, exist_ok=True)

    # Asset index
    asset_index = mc_version_json.get("assetIndex", {}).get("id", mc_version)
    assets_dir = os.path.join(game_dir, "assets")

    # Подстановки для аргументов
    replacements = {
        "${auth_player_name}": username,
        "${version_name}": mc_version,
        "${game_directory}": game_dir,
        "${assets_root}": assets_dir,
        "${assets_index_name}": asset_index,
        "${auth_uuid}": player_uuid,
        "${auth_access_token}": "0",
        "${clientid}": "",
        "${auth_xuid}": "",
        "${user_type}": "legacy",
        "${version_type}": "release",
        "${natives_directory}": natives_dir,
        "${launcher_name}": "shizoteh",
        "${launcher_version}": "1.0",
        "${classpath}": classpath,
        "${path}": natives_dir,
    }

    # Собираем команду
    cmd = [java_path]

    # JVM-аргументы
    cmd.append(f"-Xms{min_ram_mb}M")
    cmd.append(f"-Xmx{max_ram_mb}M")
    cmd.append(f"-Djava.library.path={natives_dir}")

    if extra_java_args:
        cmd.extend(extra_java_args)

    # JVM-аргументы из version.json (если есть в новом формате)
    arguments = mc_version_json.get("arguments", {})
    jvm_args_template = arguments.get("jvm", [])
    if jvm_args_template:
        jvm_args = _substitute_args(jvm_args_template, replacements)
        # Фильтруем, убираем дубликаты Xms/Xmx/library.path, т.к. мы уже их указали
        for arg in jvm_args:
            if arg.startswith("-Xms") or arg.startswith("-Xmx") or "java.library.path" in arg:
                continue
            cmd.append(arg)

    cmd.append("-cp")
    cmd.append(classpath)
    cmd.append(main_class)

    # Игровые аргументы
    game_args_template = arguments.get("game", [])
    if game_args_template:
        game_args = _substitute_args(game_args_template, replacements)
        cmd.extend(game_args)
    else:
        # Старый формат (minecraftArguments)
        mc_args_str = mc_version_json.get("minecraftArguments", "")
        if mc_args_str:
            for arg in mc_args_str.split():
                for key, val in replacements.items():
                    arg = arg.replace(key, str(val))
                cmd.append(arg)

    # Прямое подключение к серверу (в обход меню)
    cmd.extend(["--server", server_host])
    cmd.extend(["--port", str(server_port)])

    if status_callback:
        status_callback(f"Запуск Minecraft... ({username} → {server_host}:{server_port})")

    # Запускаем процесс
    process = subprocess.Popen(
        cmd,
        cwd=game_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    return process
