# -*- coding: utf-8 -*-
"""Чтение свайпов с тачскрина через Linux /dev/input/event*."""

from __future__ import annotations

import os
import re
import struct
import time
from pathlib import Path
from typing import Iterable, Optional


EV_KEY = 1
EV_ABS = 3
BTN_TOUCH = 330
ABS_X = 0
ABS_Y = 1
ABS_MT_POSITION_X = 53
ABS_MT_POSITION_Y = 54

SWIPE_LEFT = "SWIPE_LEFT"
SWIPE_RIGHT = "SWIPE_RIGHT"

EVENT_STRUCT = struct.Struct("llHHI")

TOUCH_NAME_KEYWORDS = (
    "touch",
    "touchscreen",
    "goodix",
    "ft5",
    "ft5406",
    "edt-ft5",
    "waveshare",
    "ads7846",
    "ili210x",
    "raspberrypi-ts",
    "capacitive",
    "resistive",
)


def is_touch_device_name(name: str) -> bool:
    """Проверить, похоже ли имя input-устройства на тачскрин."""

    lowered = name.lower()
    return any(keyword in lowered for keyword in TOUCH_NAME_KEYWORDS)


def event_sort_key(path: Path) -> int:
    match = re.search(r"event(\d+)$", path.name)
    if match is None:
        return 10_000
    return int(match.group(1))


def touch_event_device_from_sysfs(sys_class_input_path: Path = Path("/sys/class/input")) -> Optional[str]:
    """Найти тачскрин через sysfs event*/device/name."""

    try:
        event_dirs = sorted(sys_class_input_path.glob("event*"), key=event_sort_key)
    except OSError:
        return None

    for event_dir in event_dirs:
        try:
            name = (event_dir / "device" / "name").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if is_touch_device_name(name):
            return f"/dev/input/{event_dir.name}"
    return None


def event_handlers_from_block(block: str) -> Iterable[str]:
    match = re.search(r"^H:\s*Handlers=(?P<handlers>.+)$", block, flags=re.MULTILINE)
    if match is None:
        return ()
    return re.findall(r"\bevent\d+\b", match.group("handlers"))


def touch_event_device_from_proc_text(proc_devices_text: str) -> Optional[str]:
    """Найти тачскрин в тексте /proc/bus/input/devices."""

    for block in re.split(r"\n\s*\n", proc_devices_text):
        if not is_touch_device_name(block):
            continue
        for handler in event_handlers_from_block(block):
            return f"/dev/input/{handler}"
    return None


def touch_event_device_from_proc(
    proc_devices_path: Path = Path("/proc/bus/input/devices"),
) -> Optional[str]:
    try:
        proc_devices_text = proc_devices_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return touch_event_device_from_proc_text(proc_devices_text)


def resolve_touch_event_device(configured_device: str) -> Optional[str]:
    """Вернуть путь к event-устройству или None, если тач отключен/не найден."""

    value = configured_device.strip()
    lowered = value.lower()
    if lowered in ("", "none", "off", "disabled", "false"):
        return None
    if lowered != "auto":
        return value

    return touch_event_device_from_sysfs() or touch_event_device_from_proc()


class TouchSwipeReader:
    """Детектор горизонтальных свайпов с /dev/input/event устройства."""

    def __init__(
        self,
        device_path: str,
        min_dx: int = 1200,
        max_dt_sec: float = 2.2,
        horizontal_ratio: float = 1.1,
    ) -> None:
        self.device_path = device_path
        self.resolved_device_path: Optional[str] = None
        self.min_dx = int(min_dx)
        self.max_dt_sec = float(max_dt_sec)
        self.horizontal_ratio = float(horizontal_ratio)

        self._fd: Optional[int] = None
        self._x: Optional[int] = None
        self._y: Optional[int] = None
        self._start: Optional[tuple[int, int, float]] = None

    @property
    def enabled(self) -> bool:
        return self._fd is not None

    def fileno(self) -> Optional[int]:
        return self._fd

    def open(self) -> bool:
        """Открыть event-устройство в non-blocking режиме."""

        device_path = resolve_touch_event_device(self.device_path)
        if not device_path:
            return False

        try:
            self._fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
            self.resolved_device_path = device_path
            return True
        except OSError:
            self._fd = None
            self.resolved_device_path = None
            return False

    def close(self) -> None:
        """Закрыть event-устройство."""

        if self._fd is None:
            return

        try:
            os.close(self._fd)
        except OSError:
            pass
        finally:
            self._fd = None

    def drain_available(self) -> None:
        """Прочитать и проигнорировать накопленные события."""

        self.consume_available()

    def consume_available(self) -> Optional[str]:
        """Читать доступные input events и вернуть последнее событие свайпа."""

        if self._fd is None:
            return None

        result = None
        while True:
            try:
                data = os.read(self._fd, EVENT_STRUCT.size)
            except BlockingIOError:
                break
            except OSError:
                self.close()
                break

            if len(data) != EVENT_STRUCT.size:
                break

            _, _, etype, code, value = EVENT_STRUCT.unpack(data)
            event = self._process_event(etype, code, value)
            if event is not None:
                result = event

        return result

    def _process_event(self, etype: int, code: int, value: int) -> Optional[str]:
        if etype == EV_ABS:
            if code in (ABS_X, ABS_MT_POSITION_X):
                self._x = int(value)
            elif code in (ABS_Y, ABS_MT_POSITION_Y):
                self._y = int(value)
            return None

        if etype == EV_KEY and code == BTN_TOUCH:
            if value == 1:
                if self._x is not None and self._y is not None:
                    self._start = (self._x, self._y, time.monotonic())
                else:
                    self._start = None
                return None

            if value == 0 and self._start is not None:
                start_x, start_y, start_ts = self._start
                end_x = self._x if self._x is not None else start_x
                end_y = self._y if self._y is not None else start_y
                self._start = None

                dx = end_x - start_x
                dy = end_y - start_y
                dt = time.monotonic() - start_ts

                if dt > self.max_dt_sec:
                    return None
                if abs(dx) < self.min_dx:
                    return None
                if abs(dx) < abs(dy) * self.horizontal_ratio:
                    return None

                return SWIPE_RIGHT if dx > 0 else SWIPE_LEFT

        return None
