"""Linux network primitives used by Watchdog checks."""

from __future__ import annotations

import socket
import ssl
import subprocess
import time
from typing import Optional


def ping_host_linux(
    host: str,
    timeout_seconds: float,
    interface: Optional[str] = None,
    count: int = 1,
) -> bool:
    """Check a host with Linux iputils ping."""

    hard_timeout = max(0.001, timeout_seconds)
    packet_count = max(1, int(count))
    ping_timeout_seconds = max(1, int(hard_timeout))
    process_timeout = hard_timeout * packet_count + max(0, packet_count - 1)
    try:
        command = ["ping", "-c", str(packet_count), "-W", str(ping_timeout_seconds)]
        if interface is not None:
            command.extend(["-I", interface])
        command.append(host)

        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=process_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def check_tcp_port(host: str, port: int, timeout_seconds: float) -> bool:
    """Check that a TCP port accepts connections within timeout."""

    hard_timeout = max(0.001, timeout_seconds)
    try:
        with socket.create_connection((host, port), timeout=hard_timeout):
            return True
    except OSError:
        return False


def check_tls_handshake(host: str, port: int, timeout_seconds: float) -> bool:
    """Check TCP connect and TLS handshake with SNI."""

    hard_timeout = max(0.001, timeout_seconds)
    deadline = time.monotonic() + hard_timeout
    try:
        raw_socket = socket.create_connection((host, port), timeout=hard_timeout)
        with raw_socket:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            raw_socket.settimeout(max(0.001, remaining))

            context = ssl.create_default_context()
            with context.wrap_socket(raw_socket, server_hostname=host):
                return True
    except (OSError, TimeoutError, ssl.SSLError):
        return False
