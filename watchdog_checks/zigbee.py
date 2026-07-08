"""Zigbee checks through Home Assistant ZHA WebSocket API."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
from typing import Any, Optional


WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def check_zigbee(
    address: str,
    port: int,
    token: str,
    max_offline_devices: int,
    timeout_seconds: float,
    user_agent: str,
) -> bool:
    """Check ZHA status by counting offline devices reported by Home Assistant."""

    if not token.strip():
        return False

    devices = fetch_zha_devices(address, port, token, timeout_seconds, user_agent)
    if devices is None:
        return False

    offline_count = sum(1 for device in devices if zha_device_is_offline(device))
    return offline_count <= max_offline_devices


def zha_device_is_offline(device: dict[str, Any]) -> bool:
    """Return whether a ZHA device object reports itself as unavailable."""

    if device.get("available") is False:
        return True
    if device.get("is_available") is False:
        return True

    nested_device = device.get("device")
    return isinstance(nested_device, dict) and nested_device.get("available") is False


def fetch_zha_devices(
    address: str,
    port: int,
    token: str,
    timeout_seconds: float,
    user_agent: str,
) -> Optional[list[dict[str, Any]]]:
    """Fetch ZHA devices via Home Assistant WebSocket API."""

    hard_timeout = max(0.001, timeout_seconds)
    deadline = time.monotonic() + hard_timeout
    key = base64.b64encode(os.urandom(16)).decode("ascii")

    try:
        with socket.create_connection((address, port), timeout=hard_timeout) as sock:
            sock.settimeout(hard_timeout)
            _send_websocket_handshake(sock, address, port, key, user_agent)
            if not _read_websocket_handshake(sock, key, deadline):
                return None

            auth_required = _read_websocket_json(sock, deadline)
            if auth_required is None or auth_required.get("type") != "auth_required":
                return None

            _send_websocket_json(sock, {"type": "auth", "access_token": token})
            auth_result = _read_websocket_json(sock, deadline)
            if auth_result is None or auth_result.get("type") != "auth_ok":
                return None

            _send_websocket_json(sock, {"id": 1, "type": "zha/devices"})
            devices_result = _read_websocket_json(sock, deadline)
    except (OSError, socket.timeout, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    if devices_result is None:
        return None
    if devices_result.get("type") != "result" or not devices_result.get("success", False):
        return None

    devices = devices_result.get("result")
    if not isinstance(devices, list):
        return None
    return [device for device in devices if isinstance(device, dict)]


def _send_websocket_handshake(
    sock: socket.socket,
    address: str,
    port: int,
    key: str,
    user_agent: str,
) -> None:
    request = (
        "GET /api/websocket HTTP/1.1\r\n"
        f"Host: {address}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"User-Agent: {user_agent}\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))


def _read_websocket_handshake(sock: socket.socket, key: str, deadline: float) -> bool:
    raw_response = bytearray()
    while b"\r\n\r\n" not in raw_response:
        raw_response.extend(_recv_exact(sock, 1, deadline))
        if len(raw_response) > 8192:
            return False

    response = raw_response.decode("iso-8859-1")
    header_lines = response.split("\r\n")
    if not header_lines or " 101 " not in header_lines[0]:
        return False

    headers: dict[str, str] = {}
    for line in header_lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()

    expected_accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
    return headers.get("sec-websocket-accept") == expected_accept


def _send_websocket_json(sock: socket.socket, message: dict[str, Any]) -> None:
    _send_websocket_frame(sock, 0x1, json.dumps(message, separators=(",", ":")).encode("utf-8"))


def _send_websocket_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    header = bytearray([0x80 | opcode])
    payload_len = len(payload)
    if payload_len < 126:
        header.append(0x80 | payload_len)
    elif payload_len <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", payload_len))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", payload_len))

    mask = os.urandom(4)
    masked_payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked_payload)


def _read_websocket_json(sock: socket.socket, deadline: float) -> Optional[dict[str, Any]]:
    while True:
        opcode, payload = _read_websocket_frame(sock, deadline)
        if opcode == 0x1:
            message = json.loads(payload.decode("utf-8"))
            return message if isinstance(message, dict) else None
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            _send_websocket_frame(sock, 0xA, payload)


def _read_websocket_frame(sock: socket.socket, deadline: float) -> tuple[int, bytes]:
    first_byte, second_byte = _recv_exact(sock, 2, deadline)
    opcode = first_byte & 0x0F
    masked = bool(second_byte & 0x80)
    payload_len = second_byte & 0x7F

    if payload_len == 126:
        payload_len = struct.unpack("!H", _recv_exact(sock, 2, deadline))[0]
    elif payload_len == 127:
        payload_len = struct.unpack("!Q", _recv_exact(sock, 8, deadline))[0]

    mask = _recv_exact(sock, 4, deadline) if masked else b""
    payload = _recv_exact(sock, payload_len, deadline)
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def _recv_exact(sock: socket.socket, length: int, deadline: float) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("WebSocket read timed out")
        sock.settimeout(remaining)
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise OSError("WebSocket connection closed")
        chunks.extend(chunk)
    return bytes(chunks)
