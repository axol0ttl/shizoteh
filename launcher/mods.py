"""
mods.py — Синхронизация модов с GitHub-репозиторием.

Получает список модов из репозитория (рекурсивно обходит подпапки в MODs/),
скачивает недостающие моды, обнаруживает лишние моды у клиента.
"""

import json
import os
import re
from typing import Callable
from urllib.parse import quote

import requests

from config import GITHUB_API_BASE, GITHUB_RAW_BASE


class ModInfo:
    """Информация о моде."""

    def __init__(self, name: str, download_url: str, size: int, category: str):
        self.name = name
        self.download_url = download_url
        self.size = size
        self.category = category  # Подпапка из GitHub (для справки)

    def __repr__(self):
        return f"ModInfo({self.name}, category={self.category})"


def get_remote_mods() -> list[ModInfo]:
    """
    Получает список всех модов из GitHub-репозитория.
    Использует многоуровневый подход:
    1. GitHub Git Trees API (1 запрос на всё дерево репозитория вместо десятков)
    2. GitHub Web Scraping (если превышен лимит 60 req/h unauthenticated API)
    3. GitHub Contents API (рекурсивный обход подпапок)

    Returns:
        Список ModInfo со всеми .jar файлами.
    """
    # 1. Пробуем Git Tree API (всего 1 запрос)
    mods = _get_mods_from_git_tree()
    if mods is not None:
        return mods

    # 2. Если API вернул ошибку (например, 403 Rate Limit), парсим веб-интерфейс GitHub (без лимитов API)
    mods = _get_mods_from_web()
    if mods is not None:
        return mods

    # 3. Fallback: рекурсивный обход Contents API
    mods = []
    _scan_github_dir("MODs", mods, "")
    return mods


def _get_mods_from_git_tree() -> list[ModInfo] | None:
    """Получает список модов через Git Trees API (1 HTTP-запрос)."""
    url = f"{GITHUB_API_BASE}/git/trees/main?recursive=1"
    headers = {"User-Agent": "ShizotehLauncher"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        tree = resp.json().get("tree", [])
        mods = []
        for item in tree:
            path = item.get("path", "")
            if path.startswith("MODs/") and path.endswith(".jar") and item.get("type") == "blob":
                parts = path.split("/")
                filename = parts[-1]
                category = "/".join(parts[1:-1]) if len(parts) > 2 else "root"
                encoded_path = "/".join(quote(p) for p in parts)
                download_url = f"{GITHUB_RAW_BASE}/{encoded_path}"
                mods.append(ModInfo(
                    name=filename,
                    download_url=download_url,
                    size=item.get("size", 0),
                    category=category
                ))
        return mods
    except Exception:
        return None


def _get_mods_from_web() -> list[ModInfo] | None:
    """
    Получает список модов через веб-интерфейс GitHub (обход ограничения Rate Limit 60 req/h).
    """
    mods: list[ModInfo] = []
    try:
        _scan_github_web("MODs", mods, "")
        return mods
    except Exception:
        return None


def _scan_github_web(path: str, mods: list[ModInfo], category: str) -> None:
    """Рекурсивно сканирует директорию на GitHub через публичную веб-страницу."""
    url = f"https://github.com/axol0ttl/shizoteh/tree/main/{path}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    matches = re.findall(r'data-target="react-app\.embeddedData"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
    if not matches:
        return

    data = json.loads(matches[0])
    items = data.get("payload", {}).get("codeViewTreeRoute", {}).get("tree", {}).get("items", [])

    for item in items:
        item_path = item.get("path", "")
        item_name = item.get("name", "")
        content_type = item.get("contentType")

        if content_type == "directory":
            sub_category = item_name if not category else f"{category}/{item_name}"
            _scan_github_web(item_path, mods, sub_category)
        elif content_type == "file" and item_name.endswith(".jar"):
            parts = item_path.split("/")
            encoded_path = "/".join(quote(p) for p in parts)
            download_url = f"{GITHUB_RAW_BASE}/{encoded_path}"
            mods.append(ModInfo(
                name=item_name,
                download_url=download_url,
                size=item.get("size", 0),
                category=category or "root"
            ))


def _scan_github_dir(path: str, mods: list[ModInfo], category: str) -> None:
    """Рекурсивно сканирует директорию на GitHub через API."""
    url = f"{GITHUB_API_BASE}/contents/{path}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        items = resp.json()
    except requests.RequestException as e:
        raise ConnectionError(f"Не удалось получить содержимое {path}: {e}")

    for item in items:
        if item["type"] == "dir":
            # Рекурсивно входим в подпапку
            sub_category = item["name"] if not category else f"{category}/{item['name']}"
            _scan_github_dir(item["path"], mods, sub_category)
        elif item["type"] == "file" and item["name"].endswith(".jar"):
            mod = ModInfo(
                name=item["name"],
                download_url=item.get("download_url", f"{GITHUB_RAW_BASE}/{item['path']}"),
                size=item.get("size", 0),
                category=category or "root"
            )
            mods.append(mod)


def get_local_mods(mods_dir: str) -> list[str]:
    """
    Получает список локальных модов (имена .jar файлов).

    Args:
        mods_dir: Путь к папке mods клиента.

    Returns:
        Список имён файлов .jar.
    """
    if not os.path.exists(mods_dir):
        return []
    return [f for f in os.listdir(mods_dir) if f.endswith(".jar")]


def compare_mods(remote_mods: list[ModInfo], local_mods: list[str]) -> tuple[list[ModInfo], list[str]]:
    """
    Сравнивает моды по имени файла.

    Args:
        remote_mods: Моды из GitHub.
        local_mods: Имена локальных модов.

    Returns:
        (missing, extra):
            missing — моды, которых нет у клиента (нужно скачать)
            extra — моды у клиента, которых нет в репозитории (лишние)
    """
    remote_names = {mod.name for mod in remote_mods}
    local_names = set(local_mods)

    missing = [mod for mod in remote_mods if mod.name not in local_names]
    extra = sorted(local_names - remote_names)

    return missing, extra


def download_mod(
    mod: ModInfo,
    mods_dir: str,
    progress_callback: Callable[[str, float], None] | None = None
) -> None:
    """
    Скачивает мод в папку mods клиента.

    Args:
        mod: Информация о моде.
        mods_dir: Путь к папке mods.
        progress_callback: Функция (имя_файла, прогресс_0_1) для отображения прогресса.
    """
    os.makedirs(mods_dir, exist_ok=True)
    target_path = os.path.join(mods_dir, mod.name)

    resp = requests.get(mod.download_url, stream=True, timeout=60)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0

    with open(target_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if progress_callback and total > 0:
                progress_callback(mod.name, downloaded / total)

    if progress_callback:
        progress_callback(mod.name, 1.0)


def delete_mod(mods_dir: str, mod_name: str) -> None:
    """Удаляет мод из локальной папки."""
    path = os.path.join(mods_dir, mod_name)
    if os.path.exists(path):
        os.remove(path)


def sync_mods(
    game_dir: str,
    progress_callback: Callable[[str, float], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> tuple[list[ModInfo], list[str]]:
    """
    Синхронизирует моды: скачивает недостающие, возвращает лишние для обработки.

    Args:
        game_dir: Корневая папка игры (.shizoteh).
        progress_callback: Callback для прогресса скачивания.
        status_callback: Callback для текстового статуса.

    Returns:
        (downloaded, extra):
            downloaded — скачанные моды
            extra — лишние моды (которые нужно предложить удалить/оставить)
    """
    mods_dir = os.path.join(game_dir, "mods")

    if status_callback:
        status_callback("Получение списка модов с сервера...")

    remote_mods = get_remote_mods()
    local_mods = get_local_mods(mods_dir)
    missing, extra = compare_mods(remote_mods, local_mods)

    # Скачиваем недостающие
    downloaded = []
    for i, mod in enumerate(missing):
        if status_callback:
            status_callback(f"Скачивание ({i+1}/{len(missing)}): {mod.name}")
        download_mod(mod, mods_dir, progress_callback)
        downloaded.append(mod)

    if status_callback:
        if downloaded:
            status_callback(f"Скачано {len(downloaded)} модов")
        else:
            status_callback("Все моды актуальны")

    return downloaded, extra
