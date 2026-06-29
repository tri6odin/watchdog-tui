"""Runtime checkers for Watchdog."""

from __future__ import annotations

from typing import Callable, Mapping, Union

from watchdog_config import WatchdogConfig

from .haos import check_home_assistant_api
from .nas import check_smb_share_linux
from .network import check_tcp_port, check_tls_handshake, ping_host_linux
from .wifi import check_wifi_network_linux, check_wifi_pair
from .zigbee import check_zigbee


CheckValue = Union[bool, Mapping[str, bool]]
Checker = Callable[[], CheckValue]


def build_checkers(config: WatchdogConfig) -> Mapping[str, Checker]:
    """Build service checkers bound to a loaded runtime config."""

    return {
        "LCL NET": lambda: ping_host_linux(
            config.lcl_net.address,
            config.lcl_net.timeout_seconds,
        ),
        "INT NET": lambda: check_tls_handshake(
            config.int_net.host,
            config.int_net.port,
            config.int_net.timeout_seconds,
        ),
        "VPN": lambda: check_tls_handshake(
            config.vpn.host,
            config.vpn.port,
            config.vpn.timeout_seconds,
        ),
        "WIFI": lambda: check_wifi_pair(config.wifi, config.iot_wifi),
        "IOT WIFI": lambda: check_wifi_network_linux(
            config.iot_wifi.ssid,
            config.iot_wifi.password,
            config.iot_wifi.gateway,
            config.iot_wifi.interface,
            config.iot_wifi.connect_timeout_seconds,
            config.iot_wifi.ping_timeout_seconds,
            config.iot_wifi.disconnect_after_check,
        ),
        "NAS": lambda: check_tcp_port(
            config.nas.address,
            config.nas.port,
            config.nas.tcp_timeout_seconds,
        )
        and check_smb_share_linux(
            config.nas.address,
            config.nas.smb_user,
            config.nas.smb_pass,
            config.nas.smb_share,
            config.nas.smb_timeout_seconds,
        ),
        "HAOS": lambda: check_home_assistant_api(
            config.haos.address,
            config.haos.port,
            config.haos.token,
            config.haos.api_path,
            config.haos.timeout_seconds,
            config.global_config.http_user_agent,
        ),
        "ZIGBEE": check_zigbee,
    }
