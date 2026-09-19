"""
ШИЗОТЕХ Лаунчер — Точка входа.

Кроссплатформенный лаунчер для оффлайн Minecraft-сервера с Fabric.
"""

import sys
import os

# Добавляем директорию лаунчера в path (для PyInstaller и запуска из любого места)
if getattr(sys, 'frozen', False):
    # PyInstaller: _MEIPASS содержит путь к распакованным ресурсам
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)

from gui import ShizotehLauncher


def main():
    app = ShizotehLauncher()
    app.mainloop()


if __name__ == "__main__":
    main()
