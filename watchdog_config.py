"""
Загрузка runtime-конфига Watchdog из INI-файла.

В этом модуле нет сетевой логики. Он только читает значения из .conf и
превращает их в небольшие dataclass-объекты с понятными полями.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG_PATH = Path("watchdog.conf")


class ConfigError(ValueError):
    """Ошибка чтения или валидации конфигурации."""


@dataclass(frozen=True)
class GlobalConfig:
    http_user_agent: str


@dataclass(frozen=True)
class PingConfig:
    address: str
    timeout_seconds: float


@dataclass(frozen=True)
class TlsConfig:
    host: str
    port: int
    timeout_seconds: float


@dataclass(frozen=True)
class HaosConfig:
    address: str
    port: int
    api_path: str
    timeout_seconds: float
    token: str


@dataclass(frozen=True)
class ZigbeeConfig:
    max_offline_devices: int
    timeout_seconds: float


@dataclass(frozen=True)
class NasConfig:
    address: str
    port: int
    tcp_timeout_seconds: float
    smb_timeout_seconds: float
    smb_user: str
    smb_pass: str
    smb_share: str


@dataclass(frozen=True)
class WifiConfig:
    ssid: str
    password: str
    gateway: str
    interface: str
    connect_timeout_seconds: float
    ready_timeout_seconds: float
    ping_timeout_seconds: float
    disconnect_after_check: bool


@dataclass(frozen=True)
class TouchConfig:
    enabled: bool
    event_device: str
    swipe_min_dx: int
    swipe_max_dt_sec: float
    swipe_horizontal_ratio: float


@dataclass(frozen=True)
class WatchdogConfig:
    global_config: GlobalConfig
    lcl_net: PingConfig
    int_net: TlsConfig
    vpn: TlsConfig
    haos: HaosConfig
    zigbee: ZigbeeConfig
    nas: NasConfig
    wifi: WifiConfig
    iot_wifi: WifiConfig
    touch: TouchConfig


def load_watchdog_config(path: Path = DEFAULT_CONFIG_PATH) -> WatchdogConfig:
    """Прочитать Watchdog-конфиг из INI-файла."""

    if not path.exists():
        raise ConfigError(f"Файл конфигурации не найден: {path}")

    parser = configparser.ConfigParser()
    try:
        read_files = parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise ConfigError(f"Не удалось прочитать конфигурацию {path}: {exc}") from exc

    if not read_files:
        raise ConfigError(f"Файл конфигурации не прочитан: {path}")

    lcl_net = PingConfig(
        address=_get_str(parser, "lcl_net", "address"),
        timeout_seconds=_get_float(parser, "lcl_net", "timeout_seconds"),
    )

    return WatchdogConfig(
        global_config=GlobalConfig(
            http_user_agent=_get_str(parser, "global", "http_user_agent"),
        ),
        lcl_net=lcl_net,
        int_net=TlsConfig(
            host=_get_str(parser, "int_net", "host"),
            port=_get_int(parser, "int_net", "port"),
            timeout_seconds=_get_float(parser, "int_net", "timeout_seconds"),
        ),
        vpn=TlsConfig(
            host=_get_str(parser, "vpn", "host"),
            port=_get_int(parser, "vpn", "port"),
            timeout_seconds=_get_float(parser, "vpn", "timeout_seconds"),
        ),
        haos=HaosConfig(
            address=_get_str(parser, "haos", "address"),
            port=_get_int(parser, "haos", "port"),
            api_path=_get_str(parser, "haos", "api_path"),
            timeout_seconds=_get_float(parser, "haos", "timeout_seconds"),
            token=_get_str(parser, "haos", "token"),
        ),
        zigbee=ZigbeeConfig(
            max_offline_devices=_get_non_negative_int_default(parser, "zigbee", "max_offline_devices", 0),
            timeout_seconds=_get_float_default(parser, "zigbee", "timeout_seconds", 5.0),
        ),
        nas=NasConfig(
            address=_get_str(parser, "nas", "address"),
            port=_get_int(parser, "nas", "port"),
            tcp_timeout_seconds=_get_float(parser, "nas", "tcp_timeout_seconds"),
            smb_timeout_seconds=_get_float(parser, "nas", "smb_timeout_seconds"),
            smb_user=_get_str(parser, "nas", "smb_user"),
            smb_pass=_get_str(parser, "nas", "smb_pass"),
            smb_share=_get_str(parser, "nas", "smb_share"),
        ),
        wifi=_load_wifi_config(parser, "wifi", "example-wifi", lcl_net.address),
        iot_wifi=_load_wifi_config(parser, "iot_wifi", "example-iot", lcl_net.address),
        touch=TouchConfig(
            enabled=_get_bool_default(parser, "touch", "enabled", True),
            event_device=_get_str_default(parser, "touch", "event_device", "auto"),
            swipe_min_dx=_get_int_default(parser, "touch", "swipe_min_dx", 1200),
            swipe_max_dt_sec=_get_float_default(parser, "touch", "swipe_max_dt_sec", 2.2),
            swipe_horizontal_ratio=_get_float_default(parser, "touch", "swipe_horizontal_ratio", 1.1),
        ),
    )


def _require_section(parser: configparser.ConfigParser, section: str) -> None:
    if not parser.has_section(section):
        raise ConfigError(f"В конфигурации нет секции [{section}]")


def _load_wifi_config(
    parser: configparser.ConfigParser,
    section: str,
    default_ssid: str,
    default_gateway: str,
) -> WifiConfig:
    if not parser.has_section(section):
        return WifiConfig(
            ssid=default_ssid,
            password="",
            gateway=default_gateway,
            interface="auto",
            connect_timeout_seconds=25.0,
            ready_timeout_seconds=10.0,
            ping_timeout_seconds=2.0,
            disconnect_after_check=True,
        )

    return WifiConfig(
        ssid=_get_str_default(parser, section, "ssid", default_ssid),
        password=_get_str(parser, section, "password"),
        gateway=_get_str_default(parser, section, "gateway", default_gateway),
        interface=_get_str_default(parser, section, "interface", "auto"),
        connect_timeout_seconds=_get_float_default(parser, section, "connect_timeout_seconds", 25.0),
        ready_timeout_seconds=_get_float_default(parser, section, "ready_timeout_seconds", 10.0),
        ping_timeout_seconds=_get_float_default(parser, section, "ping_timeout_seconds", 2.0),
        disconnect_after_check=_get_bool_default(parser, section, "disconnect_after_check", True),
    )


def _get_str(parser: configparser.ConfigParser, section: str, option: str) -> str:
    _require_section(parser, section)
    if not parser.has_option(section, option):
        raise ConfigError(f"В секции [{section}] нет параметра {option}")
    value = parser.get(section, option).strip()
    if value == "":
        raise ConfigError(f"Параметр [{section}] {option} не должен быть пустым")
    return value


def _get_float(parser: configparser.ConfigParser, section: str, option: str) -> float:
    raw_value = _get_str(parser, section, option)
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigError(f"Параметр [{section}] {option} должен быть числом") from exc
    if value <= 0:
        raise ConfigError(f"Параметр [{section}] {option} должен быть больше нуля")
    return value


def _get_int(parser: configparser.ConfigParser, section: str, option: str) -> int:
    raw_value = _get_str(parser, section, option)
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"Параметр [{section}] {option} должен быть целым числом") from exc
    if value <= 0:
        raise ConfigError(f"Параметр [{section}] {option} должен быть больше нуля")
    return value


def _get_str_default(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    default: str,
) -> str:
    if not parser.has_section(section) or not parser.has_option(section, option):
        return default
    value = parser.get(section, option).strip()
    if value == "":
        raise ConfigError(f"Параметр [{section}] {option} не должен быть пустым")
    return value


def _get_float_default(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    default: float,
) -> float:
    if not parser.has_section(section) or not parser.has_option(section, option):
        return default
    raw_value = _get_str_default(parser, section, option, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigError(f"Параметр [{section}] {option} должен быть числом") from exc
    if value <= 0:
        raise ConfigError(f"Параметр [{section}] {option} должен быть больше нуля")
    return value


def _get_int_default(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    default: int,
) -> int:
    if not parser.has_section(section) or not parser.has_option(section, option):
        return default
    raw_value = _get_str_default(parser, section, option, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"Параметр [{section}] {option} должен быть целым числом") from exc
    if value <= 0:
        raise ConfigError(f"Параметр [{section}] {option} должен быть больше нуля")
    return value


def _get_non_negative_int_default(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    default: int,
) -> int:
    if not parser.has_section(section) or not parser.has_option(section, option):
        return default
    raw_value = _get_str_default(parser, section, option, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"Параметр [{section}] {option} должен быть целым числом") from exc
    if value < 0:
        raise ConfigError(f"Параметр [{section}] {option} должен быть нулем или больше")
    return value


def _get_bool_default(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    default: bool,
) -> bool:
    if not parser.has_section(section) or not parser.has_option(section, option):
        return default
    try:
        return parser.getboolean(section, option)
    except ValueError as exc:
        raise ConfigError(f"Параметр [{section}] {option} должен быть true или false") from exc
