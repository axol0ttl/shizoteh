"""
server_ping.py — Пинг Minecraft-сервера по протоколу Server List Ping (SLP).

Реализует Minecraft 1.7+ SLP протокол для получения информации о сервере:
- Онлайн/оффлайн статус
- Количество и макс. игроков
- MOTD
- Версия сервера
"""

import json
import socket
import struct
import time


class ServerInfo:
    """Информация о Minecraft-сервере."""

    def __init__(self):
        self.online = False
        self.players_online = 0
        self.players_max = 0
        self.motd = ""
        self.version_name = ""
        self.latency_ms = 0
        self.player_list: list[str] = []
        self.error = ""


def _pack_varint(value: int) -> bytes:
    """Кодирует число в формат VarInt (протокол Minecraft)."""
    result = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        result += struct.pack("B", byte)
        if value == 0:
            break
    return result


def _unpack_varint(sock: socket.socket) -> int:
    """Декодирует VarInt из сокета."""
    result = 0
    num_read = 0
    while True:
        data = sock.recv(1)
        if len(data) == 0:
            raise ConnectionError("Соединение закрыто при чтении VarInt")
        byte = data[0]
        result |= (byte & 0x7F) << (7 * num_read)
        num_read += 1
        if num_read > 5:
            raise ValueError("VarInt слишком длинный")
        if (byte & 0x80) == 0:
            break
    return result


def _pack_string(s: str) -> bytes:
    """Кодирует строку в формат Minecraft (VarInt длина + UTF-8 байты)."""
    encoded = s.encode("utf-8")
    return _pack_varint(len(encoded)) + encoded


def _read_exact(sock: socket.socket, n: int) -> bytes:
    """Читает ровно n байт из сокета."""
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Соединение закрыто при чтении данных")
        data += chunk
    return data


def ping_server(host: str, port: int = 25565, timeout: float = 5.0) -> ServerInfo:
    """
    Пингует Minecraft-сервер используя Server List Ping (SLP) протокол.

    Args:
        host: IP-адрес или доменное имя сервера.
        port: Порт сервера (по умолчанию 25565).
        timeout: Таймаут соединения в секундах.

    Returns:
        ServerInfo с данными о сервере.
    """
    info = ServerInfo()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        start_time = time.time()
        sock.connect((host, port))

        # --- Handshake Packet (ID 0x00) ---
        # Protocol version: -1 (запрос статуса), Server address, Server port, Next state: 1 (status)
        handshake_data = (
            _pack_varint(0x00) +           # Packet ID
            _pack_varint(-1) +             # Protocol version (-1 = status request)
            _pack_string(host) +           # Server address
            struct.pack(">H", port) +      # Server port (unsigned short, big-endian)
            _pack_varint(1)                # Next state: 1 = status
        )
        sock.send(_pack_varint(len(handshake_data)) + handshake_data)

        # --- Status Request Packet (ID 0x00) ---
        status_request = _pack_varint(0x00)
        sock.send(_pack_varint(len(status_request)) + status_request)

        # --- Читаем ответ ---
        _packet_length = _unpack_varint(sock)
        _packet_id = _unpack_varint(sock)

        # Читаем JSON-строку
        json_length = _unpack_varint(sock)
        json_data = _read_exact(sock, json_length)

        info.latency_ms = int((time.time() - start_time) * 1000)

        # Парсим JSON
        response = json.loads(json_data.decode("utf-8"))

        info.online = True

        # Версия
        if "version" in response:
            info.version_name = response["version"].get("name", "")

        # Игроки
        if "players" in response:
            players = response["players"]
            info.players_online = players.get("online", 0)
            info.players_max = players.get("max", 0)
            if "sample" in players:
                info.player_list = [p.get("name", "") for p in players["sample"]]

        # MOTD
        if "description" in response:
            desc = response["description"]
            if isinstance(desc, str):
                info.motd = desc
            elif isinstance(desc, dict):
                # Может быть Chat Component с "text" и "extra"
                info.motd = desc.get("text", "")
                if "extra" in desc:
                    for part in desc["extra"]:
                        if isinstance(part, dict):
                            info.motd += part.get("text", "")
                        elif isinstance(part, str):
                            info.motd += part

        sock.close()

    except (socket.timeout, socket.error, ConnectionError, OSError) as e:
        info.online = False
        info.error = str(e)
    except Exception as e:
        info.online = False
        info.error = f"Неизвестная ошибка: {e}"

    return info
