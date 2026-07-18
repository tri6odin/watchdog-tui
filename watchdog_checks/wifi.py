"""Linux Wi-Fi checks backed by NetworkManager/nmcli."""

from __future__ import annotations

import subprocess
import time
from math import ceil
from typing import Mapping, Optional

from watchdog_config import WifiConfig

from .network import arping_host_linux, ping_host_linux


DISCONNECT_TIMEOUT_SECONDS = 5.0
POLL_COMMAND_TIMEOUT_SECONDS = 1.0
MAX_DEVICE_READY_TIMEOUT_SECONDS = 10.0
MAX_SCAN_TIMEOUT_SECONDS = 10.0


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

        address = interface_ipv4_address_linux(interface, min(remaining, POLL_COMMAND_TIMEOUT_SECONDS))
        if address is not None:
            return address

        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def interface_route_uses_linux(
    host: str,
    source_address: str,
    interface: str,
    timeout_seconds: float,
) -> bool:
    """Return whether Linux can route host from source address through interface."""

    hard_timeout = max(0.001, timeout_seconds)
    try:
        result = subprocess.run(
            ["ip", "route", "get", host, "from", source_address, "oif", interface],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    if result.returncode != 0:
        return False

    parts = result.stdout.split()
    if "dev" not in parts:
        return False

    dev_index = parts.index("dev")
    if dev_index + 1 >= len(parts) or parts[dev_index + 1] != interface:
        return False

    if "src" not in parts:
        return True

    src_index = parts.index("src")
    return src_index + 1 < len(parts) and parts[src_index + 1] == source_address


def wait_for_interface_route_linux(
    host: str,
    source_address: str,
    interface: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.2,
) -> bool:
    """Wait until route to host is bound to the Wi-Fi source address/interface."""

    hard_timeout = max(0.001, timeout_seconds)
    deadline = time.monotonic() + hard_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        if interface_route_uses_linux(host, source_address, interface, min(remaining, POLL_COMMAND_TIMEOUT_SECONDS)):
            return True

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
            return False

        if interface_ipv4_address_linux(interface, min(remaining, POLL_COMMAND_TIMEOUT_SECONDS)) is None:
            return True

        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def active_wifi_ssid_linux(interface: str, timeout_seconds: float) -> Optional[str]:
    """Return currently associated SSID according to NetworkManager, with iw fallback."""

    active_connection = nmcli_output(
        ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", interface],
        timeout_seconds,
    )
    if active_connection is not None:
        profile = active_connection.strip().splitlines()[:1]
        if profile and profile[0] and profile[0] != "--":
            ssid = wifi_ssid_for_profile_linux(profile[0], timeout_seconds)
            if ssid is not None:
                return ssid
            return profile[0]

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
    """Wait until NetworkManager/iw reports the expected SSID."""

    hard_timeout = max(0.001, timeout_seconds)
    deadline = time.monotonic() + hard_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        if active_wifi_ssid_linux(interface, min(remaining, POLL_COMMAND_TIMEOUT_SECONDS)) == ssid:
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

    return str(max(1, ceil(max(0.001, timeout_seconds))))


def nmcli_run(command: list[str], timeout_seconds: float, input_text: Optional[str] = None) -> bool:
    """Run nmcli with a hard subprocess timeout."""

    hard_timeout = max(0.001, timeout_seconds)
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
    except (OSError, subprocess.SubprocessError):
        return False
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


def wifi_ssid_for_profile_linux(profile: str, timeout_seconds: float) -> Optional[str]:
    """Return configured SSID for a saved NetworkManager Wi-Fi profile."""

    profile_ssid = nmcli_output(
        ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", profile],
        timeout_seconds,
    )
    if profile_ssid is None:
        return None

    lines = profile_ssid.strip().splitlines()
    if not lines or not lines[0]:
        return None
    return lines[0]


def wifi_device_state_linux(interface: str, timeout_seconds: float) -> Optional[str]:
    """Return NetworkManager state for a Wi-Fi interface."""

    output = nmcli_output(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"], timeout_seconds)
    if output is None:
        return None

    for line in output.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == interface and parts[1] == "wifi":
            return parts[2]
    return None


def wifi_ssid_visible_linux(ssid: str, interface: str, timeout_seconds: float) -> bool:
    """Return whether NetworkManager scan cache contains the requested SSID."""

    expected = ssid.strip()
    if not expected:
        return False

    output = nmcli_output(
        ["nmcli", "-g", "SSID", "device", "wifi", "list", "ifname", interface],
        timeout_seconds,
    )
    if output is None:
        return False

    return any(line.strip() == expected for line in output.splitlines())


def wait_for_wifi_ssid_visible_linux(
    ssid: str,
    interface: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.5,
) -> bool:
    """Rescan and wait until NetworkManager can see an SSID before connecting."""

    hard_timeout = max(0.001, timeout_seconds)
    deadline = time.monotonic() + hard_timeout
    rescan_timeout = min(hard_timeout, 5.0)
    nmcli_run(
        ["nmcli", "--wait", nmcli_wait_arg(rescan_timeout), "device", "wifi", "rescan", "ifname", interface],
        rescan_timeout,
    )

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        if wifi_ssid_visible_linux(ssid, interface, min(remaining, POLL_COMMAND_TIMEOUT_SECONDS)):
            return True

        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def wait_for_wifi_device_available_linux(
    interface: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.2,
) -> bool:
    """Wait until NetworkManager considers a Wi-Fi device usable after radio on."""

    hard_timeout = max(0.001, timeout_seconds)
    deadline = time.monotonic() + hard_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        state = wifi_device_state_linux(interface, min(remaining, POLL_COMMAND_TIMEOUT_SECONDS))
        if state is not None and state not in ("unavailable", "unmanaged"):
            return True

        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def wifi_profile_for_ssid_linux(ssid: str, timeout_seconds: float) -> Optional[str]:
    """Find saved NetworkManager connection profile for an SSID."""

    expected_ssid = ssid.strip()
    if not expected_ssid:
        return None

    output = nmcli_output(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], timeout_seconds)
    if output is None:
        return None

    for line in output.splitlines():
        if ":" not in line:
            continue
        name, connection_type = line.rsplit(":", 1)
        if connection_type not in ("wifi", "802-11-wireless"):
            continue
        if wifi_ssid_for_profile_linux(name, timeout_seconds) == expected_ssid:
            return name
    return None


def connect_wifi_linux(
    ssid: str,
    password: str,
    interface: str,
    timeout_seconds: float,
) -> bool:
    """Connect through saved profile first, then fallback to device wifi connect."""

    target_ssid = ssid.strip()
    if not target_ssid:
        return False

    wait_arg = nmcli_wait_arg(timeout_seconds)
    profile = wifi_profile_for_ssid_linux(target_ssid, min(timeout_seconds, 5.0))
    if profile is not None:
        connected = nmcli_run(
            ["nmcli", "--wait", wait_arg, "connection", "up", "id", profile, "ifname", interface],
            timeout_seconds,
        )
        if connected:
            return True

    if not password:
        return False

    if not wait_for_wifi_ssid_visible_linux(target_ssid, interface, min(timeout_seconds, MAX_SCAN_TIMEOUT_SECONDS)):
        return False

    return nmcli_run(
        [
            "nmcli",
            "--wait",
            wait_arg,
            "--ask",
            "device",
            "wifi",
            "connect",
            target_ssid,
            "ifname",
            interface,
        ],
        timeout_seconds,
        input_text=f"{password}\n",
    )


def disconnect_wifi_linux(interface: str, timeout_seconds: float = DISCONNECT_TIMEOUT_SECONDS) -> bool:
    """Disconnect Wi-Fi and wait until stale IPv4 disappears."""

    nmcli_run(
        ["nmcli", "--wait", nmcli_wait_arg(timeout_seconds), "device", "disconnect", interface],
        timeout_seconds,
    )
    no_ipv4 = wait_for_interface_no_ipv4_linux(interface, timeout_seconds)
    active_ssid = active_wifi_ssid_linux(interface, 1.0)
    return no_ipv4 and active_ssid is None


def check_wifi_gateway_linux(
    ssid: str,
    gateway: str,
    wifi_interface: str,
    ready_timeout_seconds: float,
    gateway_timeout_seconds: float,
) -> bool:
    """Validate the connected Wi-Fi network before the single gateway ping."""

    target_ssid = ssid.strip()
    target_gateway = gateway.strip()
    if not target_ssid or not target_gateway:
        return False

    if not wait_for_connected_ssid_linux(wifi_interface, target_ssid, ready_timeout_seconds):
        return False

    source_address = wait_for_interface_ipv4_linux(wifi_interface, ready_timeout_seconds)
    if source_address is None:
        return False

    if not wait_for_interface_route_linux(target_gateway, source_address, wifi_interface, ready_timeout_seconds):
        return False

    arping_result = arping_host_linux(
        target_gateway,
        source_address,
        wifi_interface,
        gateway_timeout_seconds,
        count=1,
    )
    if arping_result is not None:
        return arping_result

    return ping_host_linux(target_gateway, gateway_timeout_seconds, interface=source_address, count=1)


def check_connected_wifi_network_linux(
    ssid: str,
    password: str,
    gateway: str,
    wifi_interface: str,
    connect_timeout_seconds: float,
    ready_timeout_seconds: float,
    gateway_timeout_seconds: float,
    disconnect_after_check: bool,
) -> bool:
    """Check one Wi-Fi network assuming radio is already enabled."""

    try:
        if not connect_wifi_linux(ssid, password, wifi_interface, connect_timeout_seconds):
            return False

        return check_wifi_gateway_linux(
            ssid,
            gateway,
            wifi_interface,
            ready_timeout_seconds,
            gateway_timeout_seconds,
        )
    finally:
        if disconnect_after_check:
            disconnect_wifi_linux(wifi_interface)


def check_wifi_network_linux(
    ssid: str,
    password: str,
    gateway: str,
    interface: str,
    connect_timeout_seconds: float,
    ready_timeout_seconds: float,
    gateway_timeout_seconds: float,
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
    try:
        if not radio_on:
            return False
        device_ready = wait_for_wifi_device_available_linux(
            wifi_interface,
            min(ready_timeout_seconds, MAX_DEVICE_READY_TIMEOUT_SECONDS),
        )
        if not device_ready:
            return False
        return check_connected_wifi_network_linux(
            ssid,
            password,
            gateway,
            wifi_interface,
            connect_timeout_seconds,
            ready_timeout_seconds,
            gateway_timeout_seconds,
            disconnect_after_check,
        )
    finally:
        if disconnect_after_check:
            nmcli_run(["nmcli", "--wait", "5", "radio", "wifi", "off"], 5.0)


def check_wifi_pair(wifi_config: WifiConfig, iot_wifi_config: WifiConfig) -> Mapping[str, bool]:
    """Check main and IoT Wi-Fi networks during one Wi-Fi radio wakeup."""

    interface_timeout = min(
        wifi_config.connect_timeout_seconds,
        iot_wifi_config.connect_timeout_seconds,
        5.0,
    )
    wifi_interface = detect_wifi_interface_linux(wifi_config.interface, interface_timeout)
    if wifi_interface is None:
        return {"WIFI": False, "IOT WIFI": False}

    radio_timeout = max(wifi_config.connect_timeout_seconds, iot_wifi_config.connect_timeout_seconds)
    ready_timeout = max(wifi_config.ready_timeout_seconds, iot_wifi_config.ready_timeout_seconds)
    wait_arg = nmcli_wait_arg(radio_timeout)
    radio_on = nmcli_run(["nmcli", "--wait", wait_arg, "radio", "wifi", "on"], radio_timeout)

    try:
        if not radio_on:
            return {"WIFI": False, "IOT WIFI": False}
        device_ready = wait_for_wifi_device_available_linux(
            wifi_interface,
            min(ready_timeout, MAX_DEVICE_READY_TIMEOUT_SECONDS),
        )
        if not device_ready:
            return {"WIFI": False, "IOT WIFI": False}
        wifi_ok = check_connected_wifi_network_linux(
            wifi_config.ssid,
            wifi_config.password,
            wifi_config.gateway,
            wifi_interface,
            wifi_config.connect_timeout_seconds,
            wifi_config.ready_timeout_seconds,
            wifi_config.gateway_timeout_seconds,
            wifi_config.disconnect_after_check,
        )
        iot_wifi_ok = check_connected_wifi_network_linux(
            iot_wifi_config.ssid,
            iot_wifi_config.password,
            iot_wifi_config.gateway,
            wifi_interface,
            iot_wifi_config.connect_timeout_seconds,
            iot_wifi_config.ready_timeout_seconds,
            iot_wifi_config.gateway_timeout_seconds,
            iot_wifi_config.disconnect_after_check,
        )
        return {"WIFI": wifi_ok, "IOT WIFI": iot_wifi_ok}
    finally:
        if wifi_config.disconnect_after_check or iot_wifi_config.disconnect_after_check:
            nmcli_run(["nmcli", "--wait", "5", "radio", "wifi", "off"], 5.0)
