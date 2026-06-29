"""
Core Watchdog model: statuses, services, time buckets and check orchestration.

This module intentionally knows nothing about curses, SQLite, NetworkManager,
curl or smbclient. Real runtime checks live in watchdog_checks/.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, Iterable, Mapping, Union


# ANSI palette from the original spec. curses does not use these strings
# directly, but they document the intended color semantics.
COLOR_RESET = "\033[0m"
COLOR_LABEL = "\033[37m"
COLOR_YELLOW = "\033[33m"
COLOR_RED = "\033[31m"
COLOR_GRAY = "\033[90m"
COLOR_FRAME = COLOR_GRAY


class Status(str, Enum):
    """Service state in one time interval."""

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
    """Carousel screens."""

    WEEKS = "НЕДЕЛИ"
    DAYS = "ДНИ"
    HOURS = "ЧАСЫ"
    UPDATE = "ОБНОВИТЬ"


@dataclass(frozen=True)
class Service:
    """Service description.

    code is stored in SQLite and used in logic, label is rendered on screen.
    They currently match, but the split lets UI labels change without breaking
    history rows.
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

# Update order and progress-bar order.
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

# Historical screens are read by columns: first four services on the left,
# next four on the right. This matches CHECK_ORDER.
DISPLAY_ROWS = [
    ("LCL NET", "IOT WIFI"),
    ("INT NET", "NAS"),
    ("VPN", "HAOS"),
    ("WIFI", "ZIGBEE"),
]

CAROUSEL = [Screen.WEEKS, Screen.DAYS, Screen.HOURS, Screen.UPDATE]
START_SCREEN = Screen.HOURS

BAR_WIDTH = 43
LABEL_WIDTH = 9
HALF_WIDTH = LABEL_WIDTH + BAR_WIDTH + 1
TOTAL_WIDTH = 1 + HALF_WIDTH + 1 + HALF_WIDTH + 1

TEN_MINUTES = 10 * 60
FOUR_HOURS = 4 * 60 * 60
ONE_DAY = 24 * 60 * 60
DEFAULT_UPDATE_INTERVAL_SECONDS = 10 * 60
DEFAULT_UPDATE_STEP_DELAY_SECONDS = 0.18

SCREEN_CELL_SECONDS = {
    Screen.WEEKS: ONE_DAY,
    Screen.DAYS: FOUR_HOURS,
    Screen.HOURS: TEN_MINUTES,
}

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

CheckValue = Union[bool, Mapping[str, bool]]
Checker = Callable[[], CheckValue]


def aggregate_statuses(statuses: Iterable[Status]) -> Status:
    """Aggregate several 10-minute statuses into one larger interval."""

    collected = list(statuses)
    if not collected:
        return Status.NO_DATA
    if Status.FAIL in collected:
        return Status.FAIL
    if Status.UNKNOWN in collected:
        return Status.UNKNOWN
    return Status.OK


def align_timestamp(timestamp: float, cell_seconds: int) -> int:
    """Align a timestamp to the start of a local time bucket."""

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
        return int(timestamp // cell_seconds) * cell_seconds
    return int(aligned.timestamp())


def seconds_until_next_history_bucket(now: float | None = None) -> float:
    """Return seconds until the next 10-minute history bucket."""

    current_time = time.time() if now is None else now
    current_bucket_start = align_timestamp(current_time, TEN_MINUTES)
    next_bucket_start = current_bucket_start + TEN_MINUTES
    return max(0.0, next_bucket_start - current_time)


def should_skip_by_dependency(service: str, results: Mapping[str, Status]) -> bool:
    """Return whether a service should become UNKNOWN due to dependency state."""

    if service != "LCL NET" and results.get("LCL NET") == Status.FAIL:
        return True
    return False


def status_from_check_value(value: bool) -> Status:
    return Status.OK if value else Status.FAIL


def run_service_checks(
    checkers: Mapping[str, Checker],
    progress_callback: Callable[[str, Mapping[str, Status]], None] | None = None,
    step_delay_seconds: float = DEFAULT_UPDATE_STEP_DELAY_SECONDS,
) -> Dict[str, Status]:
    """Run service checks in CHECK_ORDER.

    A checker usually returns bool. A checker may also return a mapping of
    service -> bool for grouped checks such as WIFI + IOT WIFI that share one
    physical operation.
    """

    results: Dict[str, Status] = {}
    skip_services = set()

    for service in CHECK_ORDER:
        if service in skip_services:
            continue

        if should_skip_by_dependency(service, results):
            results[service] = Status.UNKNOWN
            if progress_callback is not None:
                progress_callback(service, dict(results))
            if step_delay_seconds > 0:
                time.sleep(step_delay_seconds)
            continue

        try:
            raw_result = checkers[service]()
        except Exception:
            raw_result = False

        if isinstance(raw_result, Mapping):
            for grouped_service in CHECK_ORDER:
                if grouped_service not in raw_result:
                    continue
                results[grouped_service] = status_from_check_value(raw_result[grouped_service])
                skip_services.add(grouped_service)
                if progress_callback is not None:
                    progress_callback(grouped_service, dict(results))
                if step_delay_seconds > 0:
                    time.sleep(step_delay_seconds)
            continue

        results[service] = status_from_check_value(raw_result)

        if progress_callback is not None:
            progress_callback(service, dict(results))
        if step_delay_seconds > 0:
            time.sleep(step_delay_seconds)

    return results
