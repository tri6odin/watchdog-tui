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
class HttpConfig:
    address: str
    timeout_seconds: float


@dataclass(frozen=True)
class HaosConfig:
    address: str
    api_path: str
    timeout_seconds: float
    token: str


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
class WatchdogConfig:
    global_config: GlobalConfig
    lcl_net: PingConfig
    int_net: HttpConfig
    vpn: HttpConfig
    haos: HaosConfig
    nas: NasConfig


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

    return WatchdogConfig(
        global_config=GlobalConfig(
            http_user_agent=_get_str(parser, "global", "http_user_agent"),
        ),
        lcl_net=PingConfig(
            address=_get_str(parser, "lcl_net", "address"),
            timeout_seconds=_get_float(parser, "lcl_net", "timeout_seconds"),
        ),
        int_net=HttpConfig(
            address=_get_str(parser, "int_net", "address"),
            timeout_seconds=_get_float(parser, "int_net", "timeout_seconds"),
        ),
        vpn=HttpConfig(
            address=_get_str(parser, "vpn", "address"),
            timeout_seconds=_get_float(parser, "vpn", "timeout_seconds"),
        ),
        haos=HaosConfig(
            address=_get_str(parser, "haos", "address"),
            api_path=_get_str(parser, "haos", "api_path"),
            timeout_seconds=_get_float(parser, "haos", "timeout_seconds"),
            token=_get_str(parser, "haos", "token"),
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
    )


def _require_section(parser: configparser.ConfigParser, section: str) -> None:
    if not parser.has_section(section):
        raise ConfigError(f"В конфигурации нет секции [{section}]")


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
