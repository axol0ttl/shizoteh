# ШИЗОТЕХ — Руководство для AI-агентов

> Прочитай этот файл **полностью** перед любыми изменениями в коде.

## Обзор проекта

Кроссплатформенный Python-лаунчер для оффлайн (нелицензионного) Minecraft-сервера с Fabric Loader.
Лаунчер скачивает клиент Minecraft, устанавливает Fabric, синхронизирует моды из этого же GitHub-репозитория и запускает игру с прямым подключением к серверу.

**Репозиторий:** `axol0ttl/shizoteh`
**Лицензия:** GPL-3.0

## Архитектура

```
shizoteh/
├── launcher_config.json        # Серверный конфиг (версия MC, Fabric, IP). Читается лаунчером с GitHub.
├── MODs/                        # Моды для клиента. Подпапки = категории (для удобства владельца).
│   ├── optimization/            #   При установке на клиент ВСЕ .jar сливаются в одну плоскую папку mods/.
│   └── <другие_категории>/
├── launcher/                    # Исходники лаунчера (Python 3.12+)
│   ├── main.py                  #   Точка входа
│   ├── gui.py                   #   GUI (CustomTkinter, тёмная тема)
│   ├── config.py                #   Конфигурация (удалённая с GitHub + локальная клиентская)
│   ├── minecraft.py             #   Скачивание MC + Fabric + запуск Java-процесса
│   ├── mods.py                  #   Синхронизация модов через GitHub API
│   ├── server_ping.py           #   Пинг MC-сервера (SLP протокол)
│   ├── requirements.txt         #   Зависимости: customtkinter, requests, Pillow
│   ├── venv/                    #   Виртуальное окружение (в .gitignore)
│   └── .gitignore
├── .gitignore
└── README.md
```

## Ключевые принципы

### Два конфига — не путать!

| Конфиг | Где хранится | Что содержит | Кто редактирует |
|--------|-------------|-------------|-----------------|
| **Серверный** (`launcher_config.json`) | Корень репозитория, читается с GitHub через raw URL | `minecraft_version`, `fabric_loader_version`, `server_ip` | Владелец сервера |
| **Клиентский** (`client_config.json`) | Рядом с лаунчером, в .gitignore | `username`, `game_dir`, `min_ram_mb`, `max_ram_mb`, `java_path`, `java_args` | Создаётся автоматически при первом запуске |

> **ВАЖНО:** RAM, Java-аргументы и путь установки хранятся ТОЛЬКО в клиентском конфиге. Серверный конфиг содержит ТОЛЬКО версию, IP и версию Fabric.

### Моды

- Моды лежат в `MODs/` с подпапками-категориями (например `MODs/optimization/`, `MODs/content/`).
- Подпапки нужны **только для организации в репозитории**.
- На клиенте все `.jar` файлы из всех подпапок сливаются в одну плоскую папку `<game_dir>/mods/`.
- Сравнение модов — **по имени файла**.
- Если у клиента есть моды, которых нет в репозитории — лаунчер **спрашивает** пользователя: удалить или оставить. НЕ удаляет молча.

### Запуск Minecraft

- Используется **оффлайн-режим** (без авторизации Microsoft).
- UUID генерируется по стандарту: `uuid3(NAMESPACE_URL, "OfflinePlayer:<ник>".encode().hex())`.
- При запуске передаются аргументы `--server <host> --port <port>` для **прямого подключения** в обход главного меню.
- Модлоадер — **Fabric Loader**. Его профиль качается с `meta.fabricmc.net`.

### Пути по умолчанию (game_dir)

| ОС | Путь |
|----|------|
| Windows | `%APPDATA%/.shizoteh` |
| macOS | `~/Library/Application Support/.shizoteh` |
| Linux | `~/.shizoteh` |

## Известные проблемы и решения

### DNS / Скачивание ассетов

- Домен `resources.download.minecraft.com` **мёртв** (NXDOMAIN). Используй `resources.download.minecraft.net`.
- В `minecraft.py` встроен **fallback DNS-резолвер** через `8.8.8.8` / `1.1.1.1` на случай сломанного системного DNS.
- Все HTTP-запросы идут через `requests.Session` с `HTTPAdapter` и `Retry` (5 попыток, экспоненциальный backoff).

### Запуск лаунчера

- Запускать нужно через **venv**: `launcher/venv/bin/python3 launcher/main.py` или `cd launcher && source venv/bin/activate && python main.py`.
- Системный Python на macOS (Homebrew) не позволяет ставить пакеты глобально (`externally-managed-environment`).
- Для macOS может потребоваться `brew install python-tk@<версия>` и пересоздание venv после этого.

## Стек технологий

- **Python 3.12+**
- **CustomTkinter 6.x** — GUI (тёмная тема, встроенные виджеты)
- **requests** — HTTP (скачивание файлов, GitHub API, Mojang API)
- **Pillow** — обработка изображений
- **PyInstaller** — сборка в .exe (опционально)

## API Endpoints

| Назначение | URL |
|-----------|-----|
| Манифест версий MC | `https://piston-meta.mojang.com/mc/game/version_manifest_v2.json` |
| Ассеты MC | `https://resources.download.minecraft.net/{hash[:2]}/{hash}` |
| Fabric Loader профиль | `https://meta.fabricmc.net/v2/versions/loader/{mc_ver}/{loader_ver}/profile/json` |
| Серверный конфиг | `https://raw.githubusercontent.com/axol0ttl/shizoteh/main/launcher_config.json` |
| Список модов | `https://api.github.com/repos/axol0ttl/shizoteh/contents/MODs` (рекурсивно) |

## Правила для агентов

1. **Не добавляй** RAM/java-аргументы в серверный `launcher_config.json`.
2. **Не удаляй** лишние моды у клиента без подтверждения — всегда спрашивай.
3. **Используй** `resources.download.minecraft.net` (НЕ `.com`) для ассетов.
4. **Сохраняй** кроссплатформенность (Windows/macOS/Linux) во всех путях и командах.
5. **Не ломай** структуру модулей — каждый файл отвечает за свою область (config, mods, minecraft, server_ping, gui).
6. **GUI** — CustomTkinter с тёмной темой. Все длительные операции — в `threading.Thread(daemon=True)`.
7. Все комментарии и UI-тексты — **на русском языке**.
