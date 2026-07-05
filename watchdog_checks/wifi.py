"""Linux Wi-Fi checks backed by NetworkManager/nmcli."""

from __future__ import annotations

import subprocess
import time
from typing import Mapping, Optional

from watchdog_config import WifiConfig

from .network import ping_host_linux


DISCONNECT_TIMEOUT_SECONDS = 5.0


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

    output = nmcli_output(
        ["nmcli", "-g", "SSID", "device", "wifi", "list", "ifname", interface],
        timeout_seconds,
    )
    if output is None:
        return False

    expected = ssid.strip()
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
            return wifi_ssid_visible_linux(ssid, interface, 1.0)

        if wifi_ssid_visible_linux(ssid, interface, min(remaining, 1.0)):
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
    last_state = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        last_state = wifi_device_state_linux(interface, min(remaining, 1.0))
        if last_state is not None and last_state not in ("unavailable", "unmanaged"):
            return True

        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


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
        if wifi_ssid_for_profile_linux(name, timeout_seconds) == ssid:
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
        connected = nmcli_run(
            ["nmcli", "--wait", wait_arg, "connection", "up", "id", profile, "ifname", interface],
            timeout_seconds,
        )
        if connected:
            return True

    if not password:
        return False

    if not wait_for_wifi_ssid_visible_linux(ssid, interface, min(timeout_seconds, 10.0)):
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
            ssid,
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


def check_connected_wifi_network_linux(
    ssid: str,
    password: str,
    gateway: str,
    wifi_interface: str,
    connect_timeout_seconds: float,
    ping_timeout_seconds: float,
    disconnect_after_check: bool,
) -> bool:
    """Check one Wi-Fi network assuming radio is already enabled."""

    try:
        connected = connect_wifi_linux(ssid, password, wifi_interface, connect_timeout_seconds)
        if not connected:
            return False

        connected_to_expected = wait_for_connected_ssid_linux(
            wifi_interface,
            ssid,
            min(connect_timeout_seconds, 5.0),
        )
        if not connected_to_expected:
            return False

        source_address = wait_for_interface_ipv4_linux(wifi_interface, min(connect_timeout_seconds, 5.0))
        if source_address is None:
            return False
        return ping_host_linux(gateway, ping_timeout_seconds, interface=source_address, count=1)
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
    try:
        if not radio_on:
            return False
        device_ready = wait_for_wifi_device_available_linux(wifi_interface, min(connect_timeout_seconds, 10.0))
        if not device_ready:
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
    wait_arg = nmcli_wait_arg(radio_timeout)
    radio_on = nmcli_run(["nmcli", "--wait", wait_arg, "radio", "wifi", "on"], radio_timeout)

    try:
        if not radio_on:
            return {"WIFI": False, "IOT WIFI": False}
        device_ready = wait_for_wifi_device_available_linux(wifi_interface, min(radio_timeout, 10.0))
        if not device_ready:
            return {"WIFI": False, "IOT WIFI": False}
        wifi_ok = check_connected_wifi_network_linux(
            wifi_config.ssid,
            wifi_config.password,
            wifi_config.gateway,
            wifi_interface,
            wifi_config.connect_timeout_seconds,
            wifi_config.ping_timeout_seconds,
            wifi_config.disconnect_after_check,
        )
        iot_wifi_ok = check_connected_wifi_network_linux(
            iot_wifi_config.ssid,
            iot_wifi_config.password,
            iot_wifi_config.gateway,
            wifi_interface,
            iot_wifi_config.connect_timeout_seconds,
            iot_wifi_config.ping_timeout_seconds,
            iot_wifi_config.disconnect_after_check,
        )
        return {"WIFI": wifi_ok, "IOT WIFI": iot_wifi_ok}
    finally:
        if wifi_config.disconnect_after_check or iot_wifi_config.disconnect_after_check:
            nmcli_run(["nmcli", "--wait", "5", "radio", "wifi", "off"], 5.0)
