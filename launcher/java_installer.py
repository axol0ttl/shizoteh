"""
java_installer.py — Проверка наличия Java и её установка через Adoptium API.

Стратегия:
- Сначала проверяем java_path из клиентского конфига.
- Затем ищем java в PATH.
- Если не нашли — предлагаем скачать Eclipse Temurin (Java 25) и распаковать
  локально в <game_dir>/jvm/, не затрагивая системную установку.
"""

import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from typing import Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Eclipse Temurin (Adoptium) — Java 25
JAVA_VERSION = 25
ADOPTIUM_API = (
    "https://api.adoptium.net/v3/assets/latest/{version}/hotspot"
    "?architecture={arch}&image_type=jdk&os={os}&vendor=eclipse"
)

# Retry-сессия для скачивания
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(total=5, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=4)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


def _adoptium_os() -> str:
    """Возвращает имя ОС в формате Adoptium."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    elif system == "Darwin":
        return "mac"
    else:
        return "linux"


def _adoptium_arch() -> str:
    """Возвращает архитектуру в формате Adoptium."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    elif machine in ("aarch64", "arm64"):
        return "aarch64"
    elif machine in ("i386", "i686", "x86"):
        return "x86"
    return "x64"


def _java_executable_name() -> str:
    """Возвращает имя исполняемого файла Java."""
    return "java.exe" if platform.system() == "Windows" else "java"


def check_java(java_path: str = "java") -> tuple[bool, str]:
    """
    Проверяет наличие и работоспособность Java.

    Args:
        java_path: Путь к java (из конфига или 'java' для системного).

    Returns:
        (found: bool, version_string: str) — найдена ли Java и её версия.
    """
    try:
        result = subprocess.run(
            [java_path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # java -version пишет в stderr
        output = result.stderr or result.stdout
        if output:
            # Первая строка содержит версию: 'openjdk version "25.0.1" ...'
            first_line = output.strip().splitlines()[0]
            return True, first_line
        return result.returncode == 0, ""
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False, ""


def find_local_java(game_dir: str) -> str | None:
    """
    Ищет локально установленную Java в папке <game_dir>/jvm/.

    Returns:
        Полный путь к исполняемому файлу java, или None если не найдено.
    """
    jvm_dir = os.path.join(game_dir, "jvm")
    if not os.path.isdir(jvm_dir):
        return None

    java_exe = _java_executable_name()

    # Ищем java в подпапках jvm/ (например jvm/jdk-25+.../bin/java)
    for root, dirs, files in os.walk(jvm_dir):
        if java_exe in files:
            candidate = os.path.join(root, java_exe)
            # Убеждаемся, что это исполняемый файл в папке bin/
            if os.path.basename(root) == "bin":
                found, _ = check_java(candidate)
                if found:
                    return candidate

    return None


def get_java_download_info() -> dict:
    """
    Запрашивает у Adoptium API информацию о скачивании JDK {JAVA_VERSION}.

    Returns:
        Словарь с 'url', 'filename', 'checksum_link'.

    Raises:
        RuntimeError: Если не удалось получить данные.
    """
    url = ADOPTIUM_API.format(
        version=JAVA_VERSION,
        arch=_adoptium_arch(),
        os=_adoptium_os(),
    )
    session = _get_session()
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        raise RuntimeError(
            f"Adoptium API не вернул пакеты Java {JAVA_VERSION} "
            f"для {_adoptium_os()}/{_adoptium_arch()}"
        )

    # Берём первый подходящий релиз
    release = data[0]
    binary = release["binary"]
    package = binary["package"]

    return {
        "url": package["link"],
        "filename": package["name"],
        "size": package.get("size", 0),
        "checksum_url": package.get("checksum_link", ""),
        "version_name": release.get("release_name", f"jdk-{JAVA_VERSION}"),
    }


def install_java(
    game_dir: str,
    progress_callback: Callable[[str, float], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> str:
    """
    Скачивает и устанавливает JDK {JAVA_VERSION} локально в <game_dir>/jvm/.

    Args:
        game_dir: Корневая папка игры.
        progress_callback: Callback(label, 0..1).
        status_callback: Callback(text).

    Returns:
        Путь к исполняемому файлу java.

    Raises:
        RuntimeError: Если установка не удалась.
    """
    jvm_dir = os.path.join(game_dir, "jvm")
    os.makedirs(jvm_dir, exist_ok=True)

    # --- 1. Получаем ссылку для скачивания ---
    if status_callback:
        status_callback(f"☕ Получение ссылки для скачивания Java {JAVA_VERSION}...")

    try:
        info = get_java_download_info()
    except Exception as e:
        raise RuntimeError(f"Не удалось получить данные о Java {JAVA_VERSION}: {e}")

    download_url = info["url"]
    filename = info["filename"]
    total_size = info["size"]

    if status_callback:
        status_callback(f"☕ Скачивание {info['version_name']} ({filename})...")

    # --- 2. Скачиваем архив во временный файл ---
    session = _get_session()
    tmp_dir = tempfile.mkdtemp()
    archive_path = os.path.join(tmp_dir, filename)

    try:
        with session.get(download_url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            content_length = int(resp.headers.get("content-length", total_size or 0))
            downloaded = 0

            with open(archive_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and content_length > 0:
                        progress_callback(filename, downloaded / content_length)

        # --- 3. Распаковываем ---
        if status_callback:
            status_callback(f"☕ Распаковка Java {JAVA_VERSION}...")

        if filename.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(jvm_dir)
        elif filename.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(jvm_dir)
        elif filename.endswith(".tar.zst") or filename.endswith(".zst"):
            # zstd-архивы (новый формат Adoptium на некоторых платформах)
            try:
                import zstandard as zstd
                dctx = zstd.ZstdDecompressor()
                tar_path = archive_path.replace(".zst", "")
                with open(archive_path, "rb") as f_in, open(tar_path, "wb") as f_out:
                    dctx.copy_stream(f_in, f_out)
                with tarfile.open(tar_path, "r:") as tf:
                    tf.extractall(jvm_dir)
                os.remove(tar_path)
            except ImportError:
                raise RuntimeError(
                    "Для распаковки .zst архива требуется пакет zstandard. "
                    "Установите его командой: pip install zstandard"
                )
        else:
            raise RuntimeError(f"Неизвестный формат архива Java: {filename}")

    finally:
        # Удаляем временные файлы
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # --- 4. Ищем java в распакованной папке ---
    java_path = find_local_java(game_dir)
    if not java_path:
        raise RuntimeError(
            f"Java была распакована в {jvm_dir}, но исполняемый файл не найден. "
            f"Пожалуйста, установите Java вручную."
        )

    # На Unix — даём права на исполнение
    if platform.system() != "Windows":
        os.chmod(java_path, 0o755)

    if status_callback:
        status_callback(f"✅ Java {JAVA_VERSION} установлена: {java_path}")

    return java_path


def resolve_java_path(
    game_dir: str,
    configured_java_path: str = "java",
) -> tuple[str, bool]:
    """
    Определяет рабочий путь к Java.

    Порядок поиска:
    1. Путь из клиентского конфига (если задан явно, не 'java').
    2. Локально установленная Java в game_dir/jvm/.
    3. Системная Java ('java' в PATH).

    Returns:
        (java_path: str, found: bool)
    """
    # 1. Явно указанный путь
    if configured_java_path and configured_java_path != "java":
        found, _ = check_java(configured_java_path)
        if found:
            return configured_java_path, True

    # 2. Локальная Java в game_dir/jvm/
    local_java = find_local_java(game_dir)
    if local_java:
        found, _ = check_java(local_java)
        if found:
            return local_java, True

    # 3. Системная Java
    found, _ = check_java("java")
    if found:
        return "java", True

    return "java", False
