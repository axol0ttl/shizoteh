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
from concurrent.futures import ThreadPoolExecutor, as_completed
import platform
import socket
import subprocess
import time
import uuid
from typing import Callable
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Mojang API URLs
VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
ASSETS_BASE_URL = "https://resources.download.minecraft.net"

# Fabric Meta API
FABRIC_META_URL = "https://meta.fabricmc.net/v2/versions/loader/{mc_version}/{loader_version}/profile/json"

# Количество попыток скачивания и задержка между ними
MAX_RETRIES = 5
RETRY_BACKOFF = 1.5  # секунды (экспоненциально: 1.5, 3, 6, 12, 24)

# Fallback DNS серверы (если системный DNS не работает)
FALLBACK_DNS = ["8.8.8.8", "1.1.1.1"]

# Кеш DNS-резолвинга
_dns_cache: dict[str, str] = {}


def _resolve_host(hostname: str) -> str:
    """
    Резолвит hostname в IP-адрес. Сначала пробует системный DNS,
    при неудаче — fallback через Google/Cloudflare DNS (UDP запрос).
    """
    if hostname in _dns_cache:
        return _dns_cache[hostname]

    # Пробуем системный DNS
    try:
        ip = socket.gethostbyname(hostname)
        _dns_cache[hostname] = ip
        return ip
    except socket.gaierror:
        pass

    # Fallback: ручной DNS-запрос через UDP к 8.8.8.8 / 1.1.1.1
    import struct as _struct
    import random

    def _build_dns_query(domain: str) -> bytes:
        """Строит простой DNS A-запрос."""
        txn_id = random.randint(0, 65535)
        header = _struct.pack(">HHHHHH", txn_id, 0x0100, 1, 0, 0, 0)
        question = b""
        for part in domain.split("."):
            question += bytes([len(part)]) + part.encode()
        question += b"\x00"  # Конец имени
        question += _struct.pack(">HH", 1, 1)  # Type A, Class IN
        return header + question

    def _parse_dns_response(data: bytes) -> str | None:
        """Извлекает IP из DNS-ответа."""
        # Пропускаем header (12 байт) и question section
        pos = 12
        # Пропускаем question name
        while pos < len(data) and data[pos] != 0:
            pos += data[pos] + 1
        pos += 5  # null byte + QTYPE(2) + QCLASS(2)
        # Парсим answer records
        while pos < len(data) - 12:
            # Name (может быть pointer)
            if data[pos] & 0xC0 == 0xC0:
                pos += 2
            else:
                while pos < len(data) and data[pos] != 0:
                    pos += data[pos] + 1
                pos += 1
            if pos + 10 > len(data):
                break
            rtype, rclass, _ttl, rdlength = _struct.unpack_from(">HHIH", data, pos)
            pos += 10
            if rtype == 1 and rclass == 1 and rdlength == 4:  # A record
                ip = ".".join(str(b) for b in data[pos:pos+4])
                return ip
            pos += rdlength
        return None

    query = _build_dns_query(hostname)
    for dns_server in FALLBACK_DNS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(3)
            sock.sendto(query, (dns_server, 53))
            response, _ = sock.recvfrom(1024)
            sock.close()
            ip = _parse_dns_response(response)
            if ip:
                _dns_cache[hostname] = ip
                return ip
        except (socket.timeout, OSError):
            continue

    raise ConnectionError(f"Не удалось разрешить DNS для {hostname} ни через системный DNS, ни через fallback")


class FallbackDNSAdapter(HTTPAdapter):
    """HTTPAdapter, который подменяет hostname на IP при проблемах с DNS."""

    def send(self, request, **kwargs):
        """Перехватывает запрос: если DNS не работает, подставляет IP."""
        parsed = urlparse(request.url)
        hostname = parsed.hostname

        try:
            # Проверяем, работает ли системный DNS
            socket.gethostbyname(hostname)
        except socket.gaierror:
            # Системный DNS не работает — резолвим сами
            ip = _resolve_host(hostname)
            # Подменяем hostname на IP в URL
            new_url = request.url.replace(f"://{hostname}", f"://{ip}", 1)
            request.url = new_url
            # Добавляем Host header для корректного TLS/SNI
            request.headers["Host"] = hostname

        return super().send(request, **kwargs)


def _create_session() -> requests.Session:
    """Создаёт requests.Session с retry и fallback DNS."""
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = FallbackDNSAdapter(max_retries=retry_strategy, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# Глобальная сессия с retry и fallback DNS
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

    def _download_asset_task(asset_name, asset_info):
        sha1 = asset_info["hash"]
        prefix = sha1[:2]
        asset_path = os.path.join(objects_dir, prefix, sha1)
        _download_file(
            url=f"{ASSETS_BASE_URL}/{prefix}/{sha1}",
            path=asset_path,
            expected_sha1=sha1
        )

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = []
        for asset_name, asset_info in objects.items():
            futures.append(executor.submit(_download_asset_task, asset_name, asset_info))
            
        completed = 0
        for future in as_completed(futures):
            future.result()  # Если была ошибка скачивания, она выбросится здесь
            completed += 1
            if completed % 100 == 0 and status_callback:
                status_callback(f"Ассеты ({completed}/{total_assets})...")

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


def _rule_matches(rule: dict, features: dict[str, bool]) -> bool:
    """Проверяет, подходит ли одно правило запуска текущему окружению."""
    rule_os = rule.get("os", {})
    if rule_os:
        if rule_os.get("name") and rule_os["name"] != get_os_name():
            return False
        if rule_os.get("arch") and rule_os["arch"] != get_arch():
            return False

    rule_features = rule.get("features", {})
    return all(features.get(name, False) == expected for name, expected in rule_features.items())


def _rules_allow(rules: list[dict], features: dict[str, bool]) -> bool:
    """Применяет правила Mojang в их порядке и возвращает итоговое действие."""
    allowed = False
    for rule in rules:
        if _rule_matches(rule, features):
            allowed = rule.get("action", "allow") == "allow"
    return allowed


def _substitute_args(
    args_template: list,
    replacements: dict,
    features: dict[str, bool] | None = None,
) -> list[str]:
    """Фильтрует правила и подставляет значения переменных в аргументы запуска."""
    features = features or {}
    result = []
    for arg in args_template:
        if isinstance(arg, dict):
            rules = arg.get("rules")
            if rules and not _rules_allow(rules, features):
                continue

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
        "${quickPlayMultiplayer}": f"{server_host}:{server_port}",
    }

    # Собираем команду
    cmd = [java_path]

    # На macOS (Darwin) для LWJGL3 / GLFW обязателен аргумент -XstartOnFirstThread
    if platform.system() == "Darwin":
        cmd.append("-XstartOnFirstThread")

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
    supports_quick_play = any(
        isinstance(arg, dict)
        and any(
            isinstance(value, str) and "quickPlayMultiplayer" in value
            for value in (
            arg.get("value", []) if isinstance(arg.get("value"), list)
            else [arg.get("value", "")]
            )
        )
        for arg in game_args_template
    )
    if game_args_template:
        game_args = _substitute_args(
            game_args_template,
            replacements,
            features={
                "has_quick_plays_support": False,
                "is_quick_play_multiplayer": True,
            },
        )
        cmd.extend(game_args)
    else:
        # Старый формат (minecraftArguments)
        mc_args_str = mc_version_json.get("minecraftArguments", "")
        if mc_args_str:
            for arg in mc_args_str.split():
                for key, val in replacements.items():
                    arg = arg.replace(key, str(val))
                cmd.append(arg)

    # В новых версиях используется единственный quick-play аргумент.
    # Старые версии запускаются через устаревшие --server/--port.
    if not supports_quick_play:
        cmd.extend(["--server", server_host])
        cmd.extend(["--port", str(server_port)])

    if status_callback:
        status_callback(f"Запуск Minecraft... ({username} → {server_host}:{server_port})")

    # Запускаем процесс.
    # stdout и stderr перехватываем в PIPE для отображения в окне логов.
    # Чтение из труб ведётся в отдельных потоках (см. gui.py),
    # чтобы буферы не переполнялись и процесс не зависал.
    process = subprocess.Popen(
        cmd,
        cwd=game_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,           # Построчная буферизация
        text=True,           # Текстовый режим (str вместо bytes)
        encoding="utf-8",
        errors="replace",
    )

    return process
