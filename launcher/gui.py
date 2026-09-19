"""
gui.py — Графический интерфейс лаунчера ШИЗОТЕХ.

Использует CustomTkinter для современного тёмного UI.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from config import (
    load_client_config,
    load_remote_config,
    parse_server_address,
    save_client_config,
)
from minecraft import download_fabric, download_minecraft, launch_minecraft
from mods import delete_mod, sync_mods
from server_ping import ServerInfo, ping_server

# Цветовая схема
COLORS = {
    "bg_dark": "#0d0d0d",
    "bg_card": "#1a1a2e",
    "bg_input": "#16213e",
    "accent": "#e94560",
    "accent_hover": "#ff6b6b",
    "accent_green": "#00d474",
    "accent_yellow": "#ffd166",
    "text_primary": "#eaeaea",
    "text_secondary": "#8d99ae",
    "text_muted": "#555555",
    "border": "#2b2d42",
}


class ShizotehLauncher(ctk.CTk):
    """Главное окно лаунчера."""

    def __init__(self):
        super().__init__()

        # Настройки окна
        self.title("ШИЗОТЕХ Лаунчер")
        self.geometry("800x580")
        self.minsize(700, 520)
        self.resizable(True, True)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.configure(fg_color=COLORS["bg_dark"])

        # Состояние
        self.client_config = load_client_config()
        self.remote_config: dict | None = None
        self.server_info: ServerInfo | None = None
        self.is_working = False

        # Строим UI
        self._build_ui()

        # Загружаем конфиг и пингуем сервер
        self.after(100, self._initial_load)

    def _build_ui(self):
        """Строит интерфейс."""
        # --- Заголовок ---
        header_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=0, height=70)
        header_frame.pack(fill="x", padx=0, pady=0)
        header_frame.pack_propagate(False)

        title_label = ctk.CTkLabel(
            header_frame,
            text="⛏  ШИЗОТЕХ",
            font=ctk.CTkFont(family="Segoe UI", size=28, weight="bold"),
            text_color=COLORS["accent"],
        )
        title_label.pack(side="left", padx=24, pady=15)

        # Статус сервера (справа в хедере)
        self.server_status_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        self.server_status_frame.pack(side="right", padx=24, pady=15)

        self.server_dot = ctk.CTkLabel(
            self.server_status_frame,
            text="●",
            font=ctk.CTkFont(size=16),
            text_color=COLORS["text_muted"],
        )
        self.server_dot.pack(side="left", padx=(0, 6))

        self.server_label = ctk.CTkLabel(
            self.server_status_frame,
            text="Проверка...",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        )
        self.server_label.pack(side="left")

        # --- Основное содержимое ---
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=24, pady=(16, 12))

        # -- Левая панель: настройки --
        left_panel = ctk.CTkFrame(content_frame, fg_color=COLORS["bg_card"], corner_radius=12)
        left_panel.pack(side="left", fill="both", expand=True, padx=(0, 8))

        settings_label = ctk.CTkLabel(
            left_panel,
            text="Настройки",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        settings_label.pack(anchor="w", padx=16, pady=(16, 12))

        # Ник
        nick_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        nick_frame.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(
            nick_frame, text="Ник:", font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"]
        ).pack(anchor="w")

        self.username_entry = ctk.CTkEntry(
            nick_frame,
            placeholder_text="Введите ник...",
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            height=36,
        )
        self.username_entry.pack(fill="x", pady=(4, 0))
        if self.client_config.get("username"):
            self.username_entry.insert(0, self.client_config["username"])

        # Путь установки
        path_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        path_frame.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(
            path_frame, text="Путь установки:", font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"]
        ).pack(anchor="w")

        path_input_frame = ctk.CTkFrame(path_frame, fg_color="transparent")
        path_input_frame.pack(fill="x", pady=(4, 0))

        self.path_entry = ctk.CTkEntry(
            path_input_frame,
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            height=36,
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.path_entry.insert(0, self.client_config.get("game_dir", ""))

        browse_btn = ctk.CTkButton(
            path_input_frame,
            text="📁",
            width=40,
            height=36,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["border"],
            border_color=COLORS["border"],
            border_width=1,
            command=self._browse_folder,
        )
        browse_btn.pack(side="right")

        # RAM
        ram_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        ram_frame.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(
            ram_frame, text="Выделение RAM (МБ):", font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"]
        ).pack(anchor="w")

        ram_sliders_frame = ctk.CTkFrame(ram_frame, fg_color="transparent")
        ram_sliders_frame.pack(fill="x", pady=(4, 0))

        # Min RAM
        min_ram_frame = ctk.CTkFrame(ram_sliders_frame, fg_color="transparent")
        min_ram_frame.pack(fill="x", pady=(0, 4))

        self.min_ram_label = ctk.CTkLabel(
            min_ram_frame, text=f"Мин: {self.client_config.get('min_ram_mb', 2048)} МБ",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"]
        )
        self.min_ram_label.pack(side="left", padx=(0, 8))

        self.min_ram_slider = ctk.CTkSlider(
            min_ram_frame, from_=512, to=8192, number_of_steps=15,
            command=self._on_min_ram_change,
            fg_color=COLORS["bg_input"],
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        self.min_ram_slider.pack(side="left", fill="x", expand=True)
        self.min_ram_slider.set(self.client_config.get("min_ram_mb", 2048))

        # Max RAM
        max_ram_frame = ctk.CTkFrame(ram_sliders_frame, fg_color="transparent")
        max_ram_frame.pack(fill="x")

        self.max_ram_label = ctk.CTkLabel(
            max_ram_frame, text=f"Макс: {self.client_config.get('max_ram_mb', 4096)} МБ",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"]
        )
        self.max_ram_label.pack(side="left", padx=(0, 8))

        self.max_ram_slider = ctk.CTkSlider(
            max_ram_frame, from_=1024, to=16384, number_of_steps=15,
            command=self._on_max_ram_change,
            fg_color=COLORS["bg_input"],
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        self.max_ram_slider.pack(side="left", fill="x", expand=True)
        self.max_ram_slider.set(self.client_config.get("max_ram_mb", 4096))

        # -- Правая панель: статус модов --
        right_panel = ctk.CTkFrame(content_frame, fg_color=COLORS["bg_card"], corner_radius=12, width=260)
        right_panel.pack(side="right", fill="both", padx=(8, 0))
        right_panel.pack_propagate(False)

        mods_header = ctk.CTkLabel(
            right_panel,
            text="Моды",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        mods_header.pack(anchor="w", padx=16, pady=(16, 8))

        self.mods_textbox = ctk.CTkTextbox(
            right_panel,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLORS["bg_input"],
            text_color=COLORS["text_secondary"],
            corner_radius=8,
            state="disabled",
            wrap="word",
        )
        self.mods_textbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # --- Нижняя панель: прогресс + кнопка ---
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(fill="x", padx=24, pady=(0, 16))

        # Статус текст
        self.status_label = ctk.CTkLabel(
            bottom_frame,
            text="Готово к запуску",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.status_label.pack(fill="x", pady=(0, 6))

        # Прогресс-бар
        self.progress_bar = ctk.CTkProgressBar(
            bottom_frame,
            fg_color=COLORS["bg_input"],
            progress_color=COLORS["accent"],
            height=6,
            corner_radius=3,
        )
        self.progress_bar.pack(fill="x", pady=(0, 10))
        self.progress_bar.set(0)

        # Кнопка ИГРАТЬ
        self.play_button = ctk.CTkButton(
            bottom_frame,
            text="▶  ИГРАТЬ",
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="white",
            height=50,
            corner_radius=10,
            command=self._on_play_click,
        )
        self.play_button.pack(fill="x")

    # === Callbacks ===

    def _on_min_ram_change(self, value):
        val = int(value)
        self.min_ram_label.configure(text=f"Мин: {val} МБ")

    def _on_max_ram_change(self, value):
        val = int(value)
        self.max_ram_label.configure(text=f"Макс: {val} МБ")

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку для установки Minecraft")
        if folder:
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, folder)

    def _set_status(self, text: str):
        """Обновляет статус в UI (потокобезопасно)."""
        self.after(0, lambda: self.status_label.configure(text=text))

    def _set_progress(self, label: str, value: float):
        """Обновляет прогресс-бар (потокобезопасно)."""
        self.after(0, lambda: self.progress_bar.set(value))

    def _set_server_status(self, info: ServerInfo):
        """Обновляет статус сервера."""
        if info.online:
            self.server_dot.configure(text_color=COLORS["accent_green"])
            self.server_label.configure(
                text=f"Онлайн  {info.players_online}/{info.players_max}  ({info.latency_ms}ms)",
                text_color=COLORS["accent_green"],
            )
        else:
            self.server_dot.configure(text_color=COLORS["accent"])
            self.server_label.configure(
                text="Оффлайн",
                text_color=COLORS["accent"],
            )

    def _set_mods_list(self, text: str):
        """Обновляет список модов в textbox."""
        def _update():
            self.mods_textbox.configure(state="normal")
            self.mods_textbox.delete("1.0", "end")
            self.mods_textbox.insert("1.0", text)
            self.mods_textbox.configure(state="disabled")
        self.after(0, _update)

    def _set_working(self, working: bool):
        """Блокирует/разблокирует кнопку."""
        self.is_working = working
        def _update():
            if working:
                self.play_button.configure(
                    text="⏳  ЗАГРУЗКА...",
                    state="disabled",
                    fg_color=COLORS["text_muted"],
                )
            else:
                self.play_button.configure(
                    text="▶  ИГРАТЬ",
                    state="normal",
                    fg_color=COLORS["accent"],
                )
        self.after(0, _update)

    # === Логика ===

    def _initial_load(self):
        """Начальная загрузка конфига и пинг сервера."""
        threading.Thread(target=self._load_remote_and_ping, daemon=True).start()

    def _load_remote_and_ping(self):
        """Загружает удалённый конфиг и пингует сервер."""
        # Загрузка конфига
        try:
            self.remote_config = load_remote_config()
            mc_ver = self.remote_config.get("minecraft_version", "?")
            self._set_status(f"Версия сервера: Minecraft {mc_ver} (Fabric)")
        except Exception as e:
            self._set_status(f"⚠ Ошибка загрузки конфига: {e}")
            return

        # Пинг сервера
        server_ip = self.remote_config.get("server_ip", "")
        if server_ip:
            host, port = parse_server_address(server_ip)
            self.server_info = ping_server(host, port)
            self.after(0, lambda: self._set_server_status(self.server_info))

    def _on_play_click(self):
        """Обработчик кнопки ИГРАТЬ."""
        if self.is_working:
            return

        username = self.username_entry.get().strip()
        if not username:
            messagebox.showwarning("ШИЗОТЕХ", "Введите ник!")
            return

        if len(username) < 3 or len(username) > 16:
            messagebox.showwarning("ШИЗОТЕХ", "Ник должен быть от 3 до 16 символов!")
            return

        game_dir = self.path_entry.get().strip()
        if not game_dir:
            messagebox.showwarning("ШИЗОТЕХ", "Укажите путь установки!")
            return

        if not self.remote_config:
            messagebox.showerror("ШИЗОТЕХ", "Не удалось загрузить конфиг сервера!")
            return

        # Сохраняем клиентский конфиг
        self.client_config["username"] = username
        self.client_config["game_dir"] = game_dir
        self.client_config["min_ram_mb"] = int(self.min_ram_slider.get())
        self.client_config["max_ram_mb"] = int(self.max_ram_slider.get())
        save_client_config(self.client_config)

        # Запускаем в отдельном потоке
        threading.Thread(target=self._launch_process, daemon=True).start()

    def _launch_process(self):
        """Полный процесс: скачать MC → Fabric → моды → запустить."""
        self._set_working(True)
        self.progress_bar.set(0)

        try:
            # Загружаем актуальный конфиг с сервера
            self._set_status("🔄 Проверка конфигурации сервера...")
            try:
                self.remote_config = load_remote_config()
            except Exception as e:
                # Если офлайн, но конфиг уже был загружен ранее при старте — используем его
                if not self.remote_config:
                    raise e

            game_dir = self.client_config["game_dir"]
            mc_version = self.remote_config["minecraft_version"]
            loader_version = self.remote_config["fabric_loader_version"]
            server_ip = self.remote_config.get("server_ip", "")
            server_host, server_port = parse_server_address(server_ip)

            # 1. Скачать Minecraft
            self._set_status("📦 Скачивание Minecraft...")
            mc_version_json = download_minecraft(
                game_dir=game_dir,
                mc_version=mc_version,
                progress_callback=self._set_progress,
                status_callback=self._set_status,
            )

            # 2. Скачать Fabric
            self._set_status("🧵 Установка Fabric Loader...")
            fabric_json = download_fabric(
                game_dir=game_dir,
                mc_version=mc_version,
                loader_version=loader_version,
                status_callback=self._set_status,
                progress_callback=self._set_progress,
            )

            # 3. Синхронизация модов
            self._set_status("🔧 Проверка модов...")
            downloaded, extra = sync_mods(
                game_dir=game_dir,
                progress_callback=self._set_progress,
                status_callback=self._set_status,
            )

            # Обновляем список модов в UI
            mods_dir = os.path.join(game_dir, "mods")
            local_mods = sorted(os.listdir(mods_dir)) if os.path.exists(mods_dir) else []
            mods_text = "\n".join(f"✅ {m}" for m in local_mods if m.endswith(".jar"))
            self._set_mods_list(mods_text or "Нет модов")

            # 4. Обработка лишних модов
            if extra:
                extra_list = "\n".join(f"• {m}" for m in extra)
                answer = messagebox.askyesno(
                    "ШИЗОТЕХ — Лишние моды",
                    f"Обнаружены моды, которых нет на сервере:\n\n{extra_list}\n\n"
                    f"Удалить их?",
                )
                if answer:
                    for mod_name in extra:
                        delete_mod(mods_dir, mod_name)
                    self._set_status(f"Удалено {len(extra)} лишних модов")

            # 5. Запуск Minecraft
            self._set_status("🚀 Запуск Minecraft...")
            self.progress_bar.set(1.0)

            process = launch_minecraft(
                game_dir=game_dir,
                mc_version=mc_version,
                mc_version_json=mc_version_json,
                fabric_json=fabric_json,
                username=self.client_config["username"],
                server_host=server_host,
                server_port=server_port,
                java_path=self.client_config.get("java_path", "java"),
                min_ram_mb=self.client_config.get("min_ram_mb", 2048),
                max_ram_mb=self.client_config.get("max_ram_mb", 4096),
                extra_java_args=self.client_config.get("java_args", []),
                status_callback=self._set_status,
            )

            import time
            time.sleep(1.5)
            exit_code = process.poll()
            if exit_code is not None:
                # communicate() дочитывает stderr и забирает завершившийся процесс,
                # чтобы он не оставался зомби в системе.
                _, stderr = process.communicate(timeout=5)
                stderr_data = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
                err_msg = stderr_data if stderr_data else f"Процесс завершился с кодом {exit_code}"
                self._set_status(f"❌ Ошибка запуска (код {exit_code})")
                messagebox.showerror("ШИЗОТЕХ — Ошибка запуска", f"Minecraft завершился сразу после запуска (код {exit_code}):\n\n{err_msg[:1000]}")
            else:
                self._set_status(f"✅ Minecraft запущен! (PID: {process.pid})")

        except Exception as e:
            self._set_status(f"❌ Ошибка: {e}")
            messagebox.showerror("ШИЗОТЕХ — Ошибка", str(e))

        finally:
            self._set_working(False)
