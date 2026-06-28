"""
Базовая логика Watchdog: статусы, сервисы, проверки и агрегация истории.

Этот модуль не знает ни про curses, ни про SQLite. Его удобно тестировать
отдельно, а позже сюда же можно добавить настоящие сетевые проверки.
"""

from __future__ import annotations

import time
import subprocess
import socket
import ssl
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, Iterable, Mapping, Optional

from watchdog_config import WatchdogConfig


# ANSI-палитра из ТЗ. В curses эти строки напрямую не используются, но оставлены
# как явная документация цветов и как удобная база, если позже понадобится
# простой non-curses вывод.
COLOR_RESET = "\033[0m"
COLOR_LABEL = "\033[37m"
COLOR_YELLOW = "\033[33m"
COLOR_RED = "\033[31m"
COLOR_GRAY = "\033[90m"
COLOR_FRAME = COLOR_GRAY


class Status(str, Enum):
    """Состояние сервиса в одном временном интервале."""

    OK = "ok"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NO_DATA = "no_data"


STATUS_SYMBOL = {
    Status.OK: "■",
    Status.FAIL: "■",
    Status.UNKNOWN: "≣",
    Status.NO_DATA: " ",
}

STATUS_COLOR = {
    Status.OK: COLOR_YELLOW,
    Status.FAIL: COLOR_RED,
    Status.UNKNOWN: COLOR_GRAY,
    Status.NO_DATA: COLOR_GRAY,
}


class Screen(str, Enum):
    """Четыре экрана карусели."""

    WEEKS = "НЕДЕЛИ"
    DAYS = "ДНИ"
    HOURS = "ЧАСЫ"
    UPDATE = "ОБНОВИТЬ"


@dataclass(frozen=True)
class Service:
    """Описание сервиса.

    code хранится в базе и используется в логике, label выводится на экран.
    Сейчас они совпадают, но разделение полезно на будущее: можно поменять
    подпись в интерфейсе и не ломать историю в SQLite.
    """

    code: str
    label: str


SERVICES: Dict[str, Service] = {
    "LCL NET": Service("LCL NET", "LCL NET"),
    "INT NET": Service("INT NET", "INT NET"),
    "VPN": Service("VPN", "VPN"),
    "WIFI": Service("WIFI", "WIFI"),
    "IOT WIFI": Service("IOT WIFI", "IOT WIFI"),
    "ZIGBEE": Service("ZIGBEE", "ZIGBEE"),
    "HAOS": Service("HAOS", "HAOS"),
    "NAS": Service("NAS", "NAS"),
}

# Порядок обновления и прогресс-бара повторяет привычный визуальный порядок.
CHECK_ORDER = [
    "LCL NET",
    "INT NET",
    "VPN",
    "WIFI",
    "IOT WIFI",
    "NAS",
    "HAOS",
    "ZIGBEE",
]

# Исторические экраны читаются колонками: слева первые четыре сервиса, справа
# следующие четыре. Так получается тот же порядок, что и у обновления.
DISPLAY_ROWS = [
    ("LCL NET", "IOT WIFI"),
    ("INT NET", "NAS"),
    ("VPN", "HAOS"),
    ("WIFI", "ZIGBEE"),
]

CAROUSEL = [Screen.WEEKS, Screen.DAYS, Screen.HOURS, Screen.UPDATE]

# Главный экран после запуска. Пользователь изменил поведение с ДНИ на ЧАСЫ,
# поэтому держим это отдельной константой, а не спрятанным значением в TUI-цикле.
START_SCREEN = Screen.HOURS

# Все исторические бары имеют 43 ячейки. Меняется только смысл одной ячейки.
BAR_WIDTH = 43
LABEL_WIDTH = 9
HALF_WIDTH = LABEL_WIDTH + BAR_WIDTH + 1
TOTAL_WIDTH = 1 + HALF_WIDTH + 1 + HALF_WIDTH + 1

TEN_MINUTES = 10 * 60
FOUR_HOURS = 4 * 60 * 60
ONE_DAY = 24 * 60 * 60
DEFAULT_UPDATE_INTERVAL_SECONDS = 10 * 60
DEFAULT_UPDATE_STEP_DELAY_SECONDS = 0.18

_RUNTIME_CONFIG: Optional[WatchdogConfig] = None

SCREEN_CELL_SECONDS = {
    Screen.WEEKS: ONE_DAY,
    Screen.DAYS: FOUR_HOURS,
    Screen.HOURS: TEN_MINUTES,
}

# Цифры нижнего индекса из макета. Они занимают одну терминальную колонку и
# помогают шкале выглядеть как в ТЗ.
SUBSCRIPT_DIGITS = {
    0: "₀",
    1: "₁",
    2: "₂",
    3: "₃",
    4: "₄",
    5: "₅",
    6: "₆",
    7: "₇",
}


def configure_checks(config: WatchdogConfig) -> None:
    """Передать проверкам runtime-конфиг, загруженный из .conf файла."""

    global _RUNTIME_CONFIG
    _RUNTIME_CONFIG = config


def require_runtime_config() -> WatchdogConfig:
    """Вернуть runtime-конфиг или явно объяснить, почему проверки не стартуют."""

    if _RUNTIME_CONFIG is None:
        raise RuntimeError("Runtime-конфиг не загружен. Вызови configure_checks() перед проверками.")
    return _RUNTIME_CONFIG


def ping_host_linux(host: str, timeout_seconds: float, interface: Optional[str] = None) -> bool:
    """Проверить хост одним ICMP ping под Linux/Raspberry Pi.

    Флаги здесь намеренно Linux/iputils-специфичные:
    - -c 1 отправляет один пакет;
    - -W принимает таймаут ответа в секундах.

    timeout у subprocess равен пользовательскому таймауту. Это жесткий потолок:
    если ping по какой-то причине живет дольше, Watchdog прерывает проверку.
    """

    hard_timeout = max(0.001, timeout_seconds)
    ping_timeout_seconds = max(1, int(hard_timeout))
    try:
        command = ["ping", "-c", "1", "-W", str(ping_timeout_seconds)]
        if interface is not None:
            command.extend(["-I", interface])
        command.append(host)

        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def detect_wifi_interface_linux(configured_interface: str, timeout_seconds: float) -> Optional[str]:
    """Найти Wi-Fi интерфейс для NetworkManager/nmcli."""

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
    """nmcli принимает --wait только целым числом секунд."""

    return str(max(1, int(timeout_seconds)))


def nmcli_run(command: list[str], timeout_seconds: float, input_text: Optional[str] = None) -> bool:
    """Выполнить nmcli-команду с жестким subprocess timeout."""

    hard_timeout = max(0.001, timeout_seconds)
    try:
        result = subprocess.run(
            command,
            input=input_text,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=input_text is not None,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def check_wifi_network_linux(
    ssid: str,
    password: str,
    gateway: str,
    interface: str,
    connect_timeout_seconds: float,
    ping_timeout_seconds: float,
    disconnect_after_check: bool,
) -> bool:
    """Подключиться к Wi-Fi сети и проверить шлюз через Wi-Fi интерфейс."""

    if not ssid.strip() or not password:
        return False

    wifi_interface = detect_wifi_interface_linux(interface, min(connect_timeout_seconds, 5.0))
    if wifi_interface is None:
        return False

    wait_arg = nmcli_wait_arg(connect_timeout_seconds)
    nmcli_run(["nmcli", "--wait", wait_arg, "radio", "wifi", "on"], connect_timeout_seconds)
    connected = nmcli_run(
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
            wifi_interface,
        ],
        connect_timeout_seconds,
        input_text=f"{password}\n",
    )
    if not connected:
        return False

    try:
        return ping_host_linux(gateway, ping_timeout_seconds, interface=wifi_interface)
    finally:
        if disconnect_after_check:
            nmcli_run(
                ["nmcli", "--wait", "5", "device", "disconnect", wifi_interface],
                5.0,
            )


def check_http_endpoint(url: str, timeout_seconds: float, user_agent: str) -> bool:
    """Проверить, что HTTP endpoint отвечает нормальным статусом.

    Используем системный curl: его --max-time задает общий лимит
    операции, включая DNS/connect/TLS/redirect/read. Поверх этого subprocess
    получает такой же hard timeout, чтобы проверка точно не висела дольше
    заданного значения.
    """

    hard_timeout = max(0.001, timeout_seconds)
    timeout_arg = f"{hard_timeout:.3f}"
    try:
        result = subprocess.run(
            [
                "curl",
                "--location",
                "--silent",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--max-time",
                timeout_arg,
                "--connect-timeout",
                timeout_arg,
                "--max-redirs",
                "5",
                "--user-agent",
                user_agent,
                url,
            ],
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
    try:
        status = int(result.stdout.strip())
    except ValueError:
        return False
    return 200 <= status < 400


def join_url(base_url: str, path: str) -> str:
    """Склеить базовый URL и путь без двойных слешей."""

    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def curl_config_value(value: str) -> str:
    """Экранировать строку для значения в curl config."""

    return value.replace("\\", "\\\\").replace('"', '\\"')


def check_home_assistant_api(
    address: str,
    port: int,
    token: str,
    api_path: str,
    timeout_seconds: float,
    user_agent: str,
) -> bool:
    """Проверить Home Assistant API с Bearer token.

    Токен передаем через stdin-конфиг curl, а не через аргументы процесса. Так
    секрет не будет виден в списке процессов во время проверки.
    """

    if not token.strip():
        return False

    hard_timeout = max(0.001, timeout_seconds)
    timeout_arg = f"{hard_timeout:.3f}"
    url = join_url(f"http://{address}:{port}", api_path)
    curl_config = (
        f'header = "Authorization: Bearer {curl_config_value(token)}"\n'
        'header = "Accept: application/json"\n'
    )
    try:
        result = subprocess.run(
            [
                "curl",
                "--config",
                "-",
                "--silent",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--max-time",
                timeout_arg,
                "--connect-timeout",
                timeout_arg,
                "--user-agent",
                user_agent,
                url,
            ],
            input=curl_config,
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
    try:
        status = int(result.stdout.strip())
    except ValueError:
        return False
    return status == 200


def check_tcp_port(host: str, port: int, timeout_seconds: float) -> bool:
    """Проверить, что TCP-порт принимает соединение в пределах таймаута."""

    hard_timeout = max(0.001, timeout_seconds)
    try:
        with socket.create_connection((host, port), timeout=hard_timeout):
            return True
    except OSError:
        return False


def check_tls_handshake(host: str, port: int, timeout_seconds: float) -> bool:
    """Проверить TCP-connect и TLS handshake с SNI.

    Для VPN нам важен сам факт, что целевой сервис доступен через туннель и
    отвечает на TLS, а не то, какую HTML/HTTP-страницу отдаст Instagram.
    """

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


def smb_share_is_listed(smb_output: str, share_name: str) -> bool:
    """Проверить, что вывод smbclient показал нужную шару.

    На Raspberry Pi используем `smbclient -g`: в grepable-выводе имя шары
    обычно идет второй колонкой, например `Disk|BACKUP|...`. Для устойчивости
    также поддерживаем обычный табличный вывод, где имя шары стоит первым.
    """

    expected = share_name.strip().lower()
    for line in smb_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "|" in stripped:
            columns = [column.strip().lower() for column in stripped.split("|")]
            if expected in columns:
                return True
        else:
            columns = stripped.split()
            if columns and columns[0].lower() == expected:
                return True
    return False


def check_smb_share_linux(
    host: str,
    user: str,
    password: str,
    share: str,
    timeout_seconds: float,
) -> bool:
    """Проверить доступность SMB-сервера и наличие нужной шары через smbclient."""

    hard_timeout = max(0.001, timeout_seconds)
    env = os.environ.copy()
    env["PASSWD"] = password
    try:
        result = subprocess.run(
            [
                "smbclient",
                "-L",
                f"//{host}",
                "-U",
                user,
                "-g",
            ],
            env=env,
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
    return smb_share_is_listed(result.stdout, share)


def check_lcl_net() -> bool:
    """Проверка локальной сети через ping до домашнего шлюза."""

    config = require_runtime_config()
    return ping_host_linux(config.lcl_net.address, config.lcl_net.timeout_seconds)


def check_INT_net() -> bool:
    """Проверка внешнего интернета через TLS handshake до ya.ru."""

    config = require_runtime_config()
    return check_tls_handshake(
        config.int_net.host,
        config.int_net.port,
        config.int_net.timeout_seconds,
    )


def check_vpn() -> bool:
    """Проверка VPN-доступа через TLS handshake до instagram.com."""

    config = require_runtime_config()
    return check_tls_handshake(
        config.vpn.host,
        config.vpn.port,
        config.vpn.timeout_seconds,
    )


def check_wifi() -> bool:
    """Проверка основной Wi-Fi сети через подключение к заданному SSID."""

    config = require_runtime_config()
    return check_wifi_network_linux(
        config.wifi.ssid,
        config.wifi.password,
        config.wifi.gateway,
        config.wifi.interface,
        config.wifi.connect_timeout_seconds,
        config.wifi.ping_timeout_seconds,
        config.wifi.disconnect_after_check,
    )


def check_iot_wifi() -> bool:
    """Проверка IoT Wi-Fi сети через подключение к заданному SSID."""

    config = require_runtime_config()
    return check_wifi_network_linux(
        config.iot_wifi.ssid,
        config.iot_wifi.password,
        config.iot_wifi.gateway,
        config.iot_wifi.interface,
        config.iot_wifi.connect_timeout_seconds,
        config.iot_wifi.ping_timeout_seconds,
        config.iot_wifi.disconnect_after_check,
    )


def check_zigbee() -> bool:
    """Заглушка проверки Zigbee-инфраструктуры."""

    return True


def check_haos() -> bool:
    """Проверка Home Assistant OS через REST API."""

    config = require_runtime_config()
    return check_home_assistant_api(
        config.haos.address,
        config.haos.port,
        config.haos.token,
        config.haos.api_path,
        config.haos.timeout_seconds,
        config.global_config.http_user_agent,
    )


def check_nas() -> bool:
    """Проверка NAS: порт SMB открыт и нужная шара видна по учетке watchdog."""

    config = require_runtime_config()
    if not check_tcp_port(config.nas.address, config.nas.port, config.nas.tcp_timeout_seconds):
        return False
    return check_smb_share_linux(
        config.nas.address,
        config.nas.smb_user,
        config.nas.smb_pass,
        config.nas.smb_share,
        config.nas.smb_timeout_seconds,
    )


DEFAULT_CHECKERS: Dict[str, Callable[[], bool]] = {
    "LCL NET": check_lcl_net,
    "INT NET": check_INT_net,
    "VPN": check_vpn,
    "WIFI": check_wifi,
    "IOT WIFI": check_iot_wifi,
    "ZIGBEE": check_zigbee,
    "HAOS": check_haos,
    "NAS": check_nas,
}


def aggregate_statuses(statuses: Iterable[Status]) -> Status:
    """Собрать несколько 10-минутных статусов в один крупный интервал.

    Приоритет ровно как в ТЗ:
    FAIL > UNKNOWN > NO_DATA > OK.

    NO_DATA не хранится в базе. Он появляется только когда в интервале вообще
    нет записей. Поэтому если список пустой, возвращаем NO_DATA; если записи
    есть и среди них только OK, возвращаем OK.
    """

    collected = list(statuses)
    if not collected:
        return Status.NO_DATA
    if Status.FAIL in collected:
        return Status.FAIL
    if Status.UNKNOWN in collected:
        return Status.UNKNOWN
    return Status.OK


def align_timestamp(timestamp: float, cell_seconds: int) -> int:
    """Выровнять время на начало интервала.

    Для 10 минут, 4 часов и дня выравниваемся по локальному времени. Это
    удобнее для домашнего мониторинга: дневная ячейка начинается в местную
    полночь, а 4-часовые блоки - в 00:00, 04:00, 08:00 и так далее.
    """

    dt = datetime.fromtimestamp(timestamp)
    if cell_seconds == TEN_MINUTES:
        minute = (dt.minute // 10) * 10
        aligned = dt.replace(minute=minute, second=0, microsecond=0)
    elif cell_seconds == FOUR_HOURS:
        hour = (dt.hour // 4) * 4
        aligned = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    elif cell_seconds == ONE_DAY:
        aligned = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        # Защитный fallback для будущих режимов: простое epoch-выравнивание.
        return int(timestamp // cell_seconds) * cell_seconds
    return int(aligned.timestamp())


def seconds_until_next_history_bucket(now: Optional[float] = None) -> float:
    """Посчитать, сколько секунд осталось до следующей 10-минутной корзины.

    Экран ЧАСЫ рисует историю 10-минутными блоками. Поэтому автоматическую
    проверку лучше запускать на ближайшей границе такого блока: 12:00, 12:10,
    12:20 и так далее. Тогда новая правая ячейка закрашивается почти сразу
    после появления, а не через 10 минут от случайного момента запуска.
    """

    current_time = time.time() if now is None else now
    current_bucket_start = align_timestamp(current_time, TEN_MINUTES)
    next_bucket_start = current_bucket_start + TEN_MINUTES
    return max(0.0, next_bucket_start - current_time)


def should_skip_by_dependency(service: str, results: Mapping[str, Status]) -> bool:
    """Проверить, нужно ли пометить сервис UNKNOWN из-за зависимости."""

    if service != "LCL NET" and results.get("LCL NET") == Status.FAIL:
        return True
    return False


def run_service_checks(
    checkers: Optional[Mapping[str, Callable[[], bool]]] = None,
    progress_callback: Optional[Callable[[str, Mapping[str, Status]], None]] = None,
    step_delay_seconds: float = DEFAULT_UPDATE_STEP_DELAY_SECONDS,
) -> Dict[str, Status]:
    """Выполнить проверки строго в порядке из ТЗ.

    progress_callback нужен TUI: после каждого сервиса экран ОБНОВИТЬ
    перерисовывается, и пользователь видит, какие бары уже стали желтыми,
    красными или серыми.
    """

    active_checkers = DEFAULT_CHECKERS if checkers is None else checkers
    results: Dict[str, Status] = {}

    for service in CHECK_ORDER:
        if should_skip_by_dependency(service, results):
            status = Status.UNKNOWN
        else:
            try:
                status = Status.OK if active_checkers[service]() else Status.FAIL
            except Exception:
                # Будущие реальные проверки могут бросать исключения: timeout,
                # DNS error, permission error. Для Watchdog это обычный FAIL.
                status = Status.FAIL

        results[service] = status

        if progress_callback is not None:
            progress_callback(service, dict(results))
        if step_delay_seconds > 0:
            time.sleep(step_delay_seconds)

    return results
