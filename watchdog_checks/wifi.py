"""Linux Wi-Fi checks backed by NetworkManager/nmcli."""

from __future__ import annotations

import subprocess
import time
from typing import Mapping, Optional

from watchdog_config import WifiConfig

from .network import ping_host_linux


WIFI_DEBUG_LOG_PATH = "/tmp/watchdog-wifi-debug.log"
DISCONNECT_TIMEOUT_SECONDS = 5.0


def wifi_debug_log(message: str) -> None:
    """Temporary Wi-Fi diagnostics. Remove after Raspberry Pi debugging."""

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(WIFI_DEBUG_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def command_text(command: list[str]) -> str:
    return " ".join(command)


def interface_ipv4_address_linux(interface: str, timeout_seconds: float) -> Optional[str]:
    """Return interface IPv4 address or None if it is not assigned yet."""

    hard_timeout = max(0.001, timeout_seconds)
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", interface, "scope", "global"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        inet_index = parts.index("inet")
        if inet_index + 1 >= len(parts):
            continue
        return parts[inet_index + 1].split("/", 1)[0]
    return None


def wait_for_interface_ipv4_linux(
    interface: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.2,
) -> Optional[str]:
    """Wait for IPv4 after Wi-Fi association/DHCP."""

    hard_timeout = max(0.001, timeout_seconds)
    deadline = time.monotonic() + hard_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None

        address = interface_ipv4_address_linux(interface, min(remaining, 1.0))
        if address is not None:
            return address

        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def wait_for_interface_no_ipv4_linux(
    interface: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.2,
) -> bool:
    """Wait until interface has no global IPv4 address."""

    hard_timeout = max(0.001, timeout_seconds)
    deadline = time.monotonic() + hard_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return interface_ipv4_address_linux(interface, 1.0) is None

        if interface_ipv4_address_linux(interface, min(remaining, 1.0)) is None:
            return True

        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def active_wifi_ssid_linux(interface: str, timeout_seconds: float) -> Optional[str]:
    """Return currently associated SSID according to iw."""

    hard_timeout = max(0.001, timeout_seconds)
    try:
        result = subprocess.run(
            ["iw", "dev", interface, "link"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("SSID: "):
            return stripped.removeprefix("SSID: ")
    return None


def wait_for_connected_ssid_linux(
    interface: str,
    ssid: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.2,
) -> bool:
    """Wait until iw reports the expected SSID."""

    hard_timeout = max(0.001, timeout_seconds)
    deadline = time.monotonic() + hard_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return active_wifi_ssid_linux(interface, 1.0) == ssid

        if active_wifi_ssid_linux(interface, min(remaining, 1.0)) == ssid:
            return True

        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def detect_wifi_interface_linux(configured_interface: str, timeout_seconds: float) -> Optional[str]:
    """Find Wi-Fi interface through NetworkManager/nmcli."""

    configured = configured_interface.strip()
    if configured and configured.lower() != "auto":
        return configured

    hard_timeout = max(0.001, timeout_seconds)
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "wifi" and parts[0]:
            return parts[0]
    return None


def nmcli_wait_arg(timeout_seconds: float) -> str:
    """nmcli accepts --wait as integer seconds."""

    return str(max(1, int(timeout_seconds)))


def nmcli_run(command: list[str], timeout_seconds: float, input_text: Optional[str] = None) -> bool:
    """Run nmcli with a hard subprocess timeout."""

    hard_timeout = max(0.001, timeout_seconds)
    wifi_debug_log(f"run: {command_text(command)} timeout={hard_timeout:.3f}")
    try:
        result = subprocess.run(
            command,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        wifi_debug_log(f"run fail: {command_text(command)} error={type(exc).__name__}")
        return False
    wifi_debug_log(f"run done: {command_text(command)} rc={result.returncode}")
    if result.returncode != 0:
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stdout:
            wifi_debug_log(f"stdout: {stdout[:300]}")
        if stderr:
            wifi_debug_log(f"stderr: {stderr[:300]}")
    return result.returncode == 0


def nmcli_output(command: list[str], timeout_seconds: float) -> Optional[str]:
    """Run nmcli and return stdout when command succeeds."""

    hard_timeout = max(0.001, timeout_seconds)
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def wifi_profile_for_ssid_linux(ssid: str, timeout_seconds: float) -> Optional[str]:
    """Find saved NetworkManager connection profile for an SSID."""

    output = nmcli_output(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], timeout_seconds)
    if output is None:
        return None

    for line in output.splitlines():
        if ":" not in line:
            continue
        name, connection_type = line.rsplit(":", 1)
        if connection_type not in ("wifi", "802-11-wireless"):
            continue
        profile_ssid = nmcli_output(
            ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", name],
            timeout_seconds,
        )
        if profile_ssid is not None and profile_ssid.strip().splitlines()[:1] == [ssid]:
            return name
    return None


def connect_wifi_linux(
    ssid: str,
    password: str,
    interface: str,
    timeout_seconds: float,
) -> bool:
    """Connect through saved profile first, then fallback to device wifi connect."""

    wait_arg = nmcli_wait_arg(timeout_seconds)
    profile = wifi_profile_for_ssid_linux(ssid, min(timeout_seconds, 5.0))
    if profile is not None:
        wifi_debug_log(f"connect: ssid={ssid} profile={profile} interface={interface}")
        connected = nmcli_run(
            ["nmcli", "--wait", wait_arg, "connection", "up", "id", profile, "ifname", interface],
            timeout_seconds,
        )
        if connected:
            return True
        wifi_debug_log(f"connect profile failed: ssid={ssid} profile={profile}")

    if not password:
        wifi_debug_log(f"connect fail: ssid={ssid} no password and no working profile")
        return False

    wifi_debug_log(f"connect fallback: ssid={ssid} interface={interface}")
    return nmcli_run(
        [
            "nmcli",
            "--wait",
            wait_arg,
            "--ask",
            "device",
            "wifi",
            "connect",
            ssid,
            "ifname",
            interface,
        ],
        timeout_seconds,
        input_text=f"{password}\n",
    )


def disconnect_wifi_linux(interface: str, timeout_seconds: float = DISCONNECT_TIMEOUT_SECONDS) -> bool:
    """Disconnect Wi-Fi and wait until stale IPv4 disappears."""

    disconnected = nmcli_run(
        ["nmcli", "--wait", nmcli_wait_arg(timeout_seconds), "device", "disconnect", interface],
        timeout_seconds,
    )
    no_ipv4 = wait_for_interface_no_ipv4_linux(interface, timeout_seconds)
    active_ssid = active_wifi_ssid_linux(interface, 1.0)
    wifi_debug_log(
        f"disconnect state: interface={interface} command_ok={disconnected} "
        f"no_ipv4={no_ipv4} active_ssid={active_ssid}"
    )
    return no_ipv4 and active_ssid is None


def check_connected_wifi_network_linux(
    ssid: str,
    password: str,
    gateway: str,
    wifi_interface: str,
    connect_timeout_seconds: float,
    ping_timeout_seconds: float,
    disconnect_after_check: bool,
    service_label: str = "WIFI",
) -> bool:
    """Check one Wi-Fi network assuming radio is already enabled."""

    wifi_debug_log(f"{service_label}: start ssid={ssid} gateway={gateway} interface={wifi_interface}")
    try:
        connected = connect_wifi_linux(ssid, password, wifi_interface, connect_timeout_seconds)
        if not connected:
            wifi_debug_log(f"{service_label}: fail connect ssid={ssid}")
            return False

        connected_to_expected = wait_for_connected_ssid_linux(
            wifi_interface,
            ssid,
            min(connect_timeout_seconds, 5.0),
        )
        active_ssid = active_wifi_ssid_linux(wifi_interface, 1.0)
        wifi_debug_log(
            f"{service_label}: ssid check expected={ssid} active={active_ssid} ok={connected_to_expected}"
        )
        if not connected_to_expected:
            return False

        source_address = wait_for_interface_ipv4_linux(wifi_interface, min(connect_timeout_seconds, 5.0))
        if source_address is None:
            wifi_debug_log(f"{service_label}: fail ip ssid={ssid}")
            return False
        wifi_debug_log(f"{service_label}: ip={source_address}")
        ping_ok = ping_host_linux(gateway, ping_timeout_seconds, interface=source_address, count=1)
        wifi_debug_log(f"{service_label}: ping gateway={gateway} source={source_address} ok={ping_ok}")
        return ping_ok
    finally:
        if disconnect_after_check:
            disconnect_wifi_linux(wifi_interface)


def check_wifi_network_linux(
    ssid: str,
    password: str,
    gateway: str,
    interface: str,
    connect_timeout_seconds: float,
    ping_timeout_seconds: float,
    disconnect_after_check: bool,
) -> bool:
    """Connect to a Wi-Fi network and check gateway through Wi-Fi source IP."""

    if not ssid.strip():
        return False

    wifi_interface = detect_wifi_interface_linux(interface, min(connect_timeout_seconds, 5.0))
    if wifi_interface is None:
        return False

    wait_arg = nmcli_wait_arg(connect_timeout_seconds)
    radio_on = nmcli_run(["nmcli", "--wait", wait_arg, "radio", "wifi", "on"], connect_timeout_seconds)
    wifi_debug_log(f"WIFI: radio on ok={radio_on}")
    try:
        if not radio_on:
            return False
        return check_connected_wifi_network_linux(
            ssid,
            password,
            gateway,
            wifi_interface,
            connect_timeout_seconds,
            ping_timeout_seconds,
            disconnect_after_check,
        )
    finally:
        if disconnect_after_check:
            radio_off = nmcli_run(["nmcli", "--wait", "5", "radio", "wifi", "off"], 5.0)
            wifi_debug_log(f"WIFI: radio off ok={radio_off}")


def check_wifi_pair(wifi_config: WifiConfig, iot_wifi_config: WifiConfig) -> Mapping[str, bool]:
    """Check main and IoT Wi-Fi networks during one Wi-Fi radio wakeup."""

    wifi_debug_log("PAIR: start")
    interface_timeout = min(
        wifi_config.connect_timeout_seconds,
        iot_wifi_config.connect_timeout_seconds,
        5.0,
    )
    wifi_interface = detect_wifi_interface_linux(wifi_config.interface, interface_timeout)
    if wifi_interface is None:
        wifi_debug_log("PAIR: fail detect interface")
        return {"WIFI": False, "IOT WIFI": False}
    wifi_debug_log(f"PAIR: interface={wifi_interface}")

    radio_timeout = max(wifi_config.connect_timeout_seconds, iot_wifi_config.connect_timeout_seconds)
    wait_arg = nmcli_wait_arg(radio_timeout)
    radio_on = nmcli_run(["nmcli", "--wait", wait_arg, "radio", "wifi", "on"], radio_timeout)
    wifi_debug_log(f"PAIR: radio on ok={radio_on}")

    try:
        if not radio_on:
            return {"WIFI": False, "IOT WIFI": False}
        wifi_ok = check_connected_wifi_network_linux(
            wifi_config.ssid,
            wifi_config.password,
            wifi_config.gateway,
            wifi_interface,
            wifi_config.connect_timeout_seconds,
            wifi_config.ping_timeout_seconds,
            wifi_config.disconnect_after_check,
            "WIFI",
        )
        iot_wifi_ok = check_connected_wifi_network_linux(
            iot_wifi_config.ssid,
            iot_wifi_config.password,
            iot_wifi_config.gateway,
            wifi_interface,
            iot_wifi_config.connect_timeout_seconds,
            iot_wifi_config.ping_timeout_seconds,
            iot_wifi_config.disconnect_after_check,
            "IOT WIFI",
        )
        wifi_debug_log(f"PAIR: done WIFI={wifi_ok} IOT_WIFI={iot_wifi_ok}")
        return {"WIFI": wifi_ok, "IOT WIFI": iot_wifi_ok}
    finally:
        if wifi_config.disconnect_after_check or iot_wifi_config.disconnect_after_check:
            radio_off = nmcli_run(["nmcli", "--wait", "5", "radio", "wifi", "off"], 5.0)
            wifi_debug_log(f"PAIR: radio off ok={radio_off}")
