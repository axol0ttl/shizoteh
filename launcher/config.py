"""
config.py — Управление конфигурацией лаунчера ШИЗОТЕХ.

Удалённый конфиг (launcher_config.json): версия MC, Fabric, IP сервера.
Локальный конфиг (client_config.json): ник, путь установки, RAM, java-аргументы.
"""

import json
import os
import platform
import sys

import requests

# GitHub raw URL для серверного конфига
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/axol0ttl/shizoteh/main"
REMOTE_CONFIG_URL = f"{GITHUB_RAW_BASE}/launcher_config.json"

# GitHub API URL для содержимого репозитория
GITHUB_API_BASE = "https://api.github.com/repos/axol0ttl/shizoteh"


def get_default_game_dir() -> str:
    """Возвращает путь установки по умолчанию в зависимости от ОС."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(appdata, ".shizoteh")
    elif system == "Darwin":  # macOS
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", ".shizoteh")
    else:  # Linux и другие
        return os.path.join(os.path.expanduser("~"), ".shizoteh")


def get_client_config_path() -> str:
    """Путь к локальному клиентскому конфигу (рядом с лаунчером или в домашней директории)."""
    # Храним конфиг рядом с исполняемым файлом / скриптом
    if getattr(sys, 'frozen', False):
        # Собранный PyInstaller exe
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "client_config.json")


# Значения по умолчанию для клиентского конфига
DEFAULT_CLIENT_CONFIG = {
    "username": "",
    "game_dir": get_default_game_dir(),
    "min_ram_mb": 2048,
    "max_ram_mb": 4096,
    "java_path": "java",
    "java_args": [
        "-XX:+UseG1GC",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:G1NewSizePercent=20",
        "-XX:G1ReservePercent=20",
        "-XX:MaxGCPauseMillis=50",
        "-XX:G1HeapRegionSize=32M"
    ],
    "extra_mods_action": "ask"  # "ask", "keep", "delete"
}


def load_remote_config() -> dict:
    """
    Загружает серверный конфиг с GitHub.
    Возвращает словарь с полями: minecraft_version, fabric_loader_version, server_ip.
    """
    import time
    try:
        # Добавляем timestamp и no-cache заголовки для обхода Fastly/GitHub CDN кеша (max-age=300)
        params = {"t": int(time.time())}
        headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
        resp = requests.get(REMOTE_CONFIG_URL, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        config = resp.json()
        return config
    except requests.RequestException as e:
        raise ConnectionError(f"Не удалось загрузить конфиг с GitHub: {e}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Ошибка парсинга конфига с GitHub: {e}")


def load_client_config() -> dict:
    """Загружает локальный клиентский конфиг. Создаёт дефолтный, если не существует."""
    config_path = get_client_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # Дополняем недостающими полями из дефолта
            config = DEFAULT_CLIENT_CONFIG.copy()
            config.update(saved)
            return config
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_CLIENT_CONFIG.copy()


def save_client_config(config: dict) -> None:
    """Сохраняет клиентский конфиг на диск."""
    config_path = get_client_config_path()
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def parse_server_address(server_ip: str) -> tuple[str, int]:
    """Парсит строку 'host:port' в кортеж (host, port). По умолчанию порт 25565."""
    if ":" in server_ip:
        parts = server_ip.rsplit(":", 1)
        host = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            port = 25565
    else:
        host = server_ip
        port = 25565
    return host, port
