"""
curses-интерфейс Watchdog.

Здесь собраны только отрисовка, обработка клавиш и сценарий обновления экрана.
SQLite и сами проверки вынесены в отдельные модули, чтобы TUI не разрастался.
"""

from __future__ import annotations

import curses
import time
from datetime import datetime
from typing import Iterable, Mapping, MutableMapping, Optional

from touch_swipe import SWIPE_LEFT, SWIPE_RIGHT, TouchSwipeReader
from watchdog_config import TouchConfig
from watchdog_core import (
    BAR_WIDTH,
    CAROUSEL,
    CHECK_ORDER,
    Checker,
    DEFAULT_UPDATE_STEP_DELAY_SECONDS,
    DISPLAY_ROWS,
    HALF_WIDTH,
    LABEL_WIDTH,
    SERVICES,
    START_SCREEN,
    STATUS_SYMBOL,
    SCREEN_CELL_SECONDS,
    SUBSCRIPT_DIGITS,
    TOTAL_WIDTH,
    TEN_MINUTES,
    Screen,
    Status,
    align_timestamp,
    run_service_checks,
    seconds_until_next_history_bucket,
)
from watchdog_db import WatchdogDB


def scale_for_screen(screen: Screen) -> str:
    """Сформировать верхнюю шкалу длиной ровно 43 символа."""

    chars = [" "] * BAR_WIDTH
    if screen == Screen.WEEKS:
        # 43 дня = 6 недель и 1 день: метки 6..0 через 7 ячеек.
        for number, position in zip(range(6, -1, -1), range(0, BAR_WIDTH, 7)):
            chars[position] = SUBSCRIPT_DIGITS[number]
    else:
        # ДНИ: крупное деление = 1 день = 6 ячеек по 4 часа.
        # ЧАСЫ: крупное деление = 1 час = 6 ячеек по 10 минут.
        for number, position in zip(range(7, -1, -1), range(0, BAR_WIDTH, 6)):
            chars[position] = SUBSCRIPT_DIGITS[number]
    return "".join(chars)


def navigation_line(active: Screen) -> str:
    """Строка навигации внизу экрана."""

    parts = []
    for screen in CAROUSEL:
        marker = "▶" if screen == active else "▷"
        parts.append(f"{marker} {screen.value}")
    return "  " + "  ".join(parts)


def screen_last_update_at(screen: Screen, now: Optional[float] = None) -> int:
    """Вернуть момент последнего обновления ячейки выбранного экрана."""

    current_time = time.time() if now is None else now
    return align_timestamp(current_time, SCREEN_CELL_SECONDS[screen])


def format_last_update_time(updated_at: int) -> str:
    return datetime.fromtimestamp(updated_at).strftime("%H:%M")


def footer_line(active: Screen, now: Optional[float] = None) -> str:
    """Строка навигации с временем последнего обновления справа."""

    navigation = navigation_line(active)
    if active == Screen.UPDATE:
        return navigation[:TOTAL_WIDTH].ljust(TOTAL_WIDTH)

    last_update = f"ПОСЛЕДНЕЕ ОБНОВЛЕНИЕ В {format_last_update_time(screen_last_update_at(active, now))}"
    spacing = max(1, TOTAL_WIDTH - len(navigation) - len(last_update))
    return (navigation + " " * spacing + last_update)[:TOTAL_WIDTH]


def format_label(service: str) -> str:
    """Подогнать имя сервиса к ширине подписи из макета."""

    return SERVICES[service].label.ljust(LABEL_WIDTH)


def attr_for_status(status: Status, colors: Mapping[str, int]) -> int:
    """Вернуть curses-атрибут для конкретного статуса."""

    if status == Status.OK:
        return colors["yellow"]
    if status == Status.FAIL:
        return colors["red"]
    return colors["gray"]


def addstr_safe(stdscr: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    """Безопасно вывести строку.

    curses бросает ошибку, если писать в правый нижний угол терминала. Для TUI
    это не критично: при маленьком окне мы показываем отдельное предупреждение,
    а при нормальном размере просто игнорируем редкий крайний случай.
    """

    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def draw_status_bar(
    stdscr: curses.window,
    y: int,
    x: int,
    statuses: Iterable[Status],
    colors: Mapping[str, int],
) -> None:
    """Нарисовать 43 исторические ячейки с индивидуальными цветами."""

    for offset, status in enumerate(statuses):
        addstr_safe(
            stdscr,
            y,
            x + offset,
            STATUS_SYMBOL[status],
            attr_for_status(status, colors),
        )


def draw_history_screen(
    stdscr: curses.window,
    db: WatchdogDB,
    screen: Screen,
    colors: Mapping[str, int],
) -> None:
    """Нарисовать один из исторических экранов: НЕДЕЛИ, ДНИ или ЧАСЫ."""

    stdscr.erase()
    history = db.load_history(screen)
    scale = scale_for_screen(screen)
    scale_line = " " * (1 + LABEL_WIDTH) + scale + " " * (1 + 1 + LABEL_WIDTH) + scale + " " * 2
    top = "╭" + "─" * HALF_WIDTH + "┼" + "─" * HALF_WIDTH + "╮"
    separator = "├" + "─" * HALF_WIDTH + "┼" + "─" * HALF_WIDTH + "┤"
    bottom = "╰" + "─" * HALF_WIDTH + "┴" + "─" * HALF_WIDTH + "╯"

    addstr_safe(stdscr, 0, 0, scale_line, colors["frame"])
    addstr_safe(stdscr, 1, 0, top, colors["frame"])

    for row_index, (left_service, right_service) in enumerate(DISPLAY_ROWS):
        y = 2 + row_index * 2

        addstr_safe(stdscr, y, 0, "⎪", colors["frame"])
        addstr_safe(stdscr, y, 1, format_label(left_service), colors["label"])
        draw_status_bar(stdscr, y, 1 + LABEL_WIDTH, history[left_service], colors)
        addstr_safe(stdscr, y, 1 + LABEL_WIDTH + BAR_WIDTH, " ", colors["frame"])
        addstr_safe(stdscr, y, 1 + HALF_WIDTH, "⎪", colors["frame"])

        addstr_safe(stdscr, y, 2 + HALF_WIDTH, format_label(right_service), colors["label"])
        draw_status_bar(stdscr, y, 2 + HALF_WIDTH + LABEL_WIDTH, history[right_service], colors)
        addstr_safe(stdscr, y, 2 + HALF_WIDTH + LABEL_WIDTH + BAR_WIDTH, " ", colors["frame"])
        addstr_safe(stdscr, y, TOTAL_WIDTH - 1, "⎪", colors["frame"])

        if row_index < len(DISPLAY_ROWS) - 1:
            addstr_safe(stdscr, y + 1, 0, separator, colors["frame"])

    addstr_safe(stdscr, 9, 0, bottom, colors["frame"])
    addstr_safe(stdscr, 10, 0, footer_line(screen), colors["label"])
    stdscr.refresh()


def draw_update_screen(
    stdscr: curses.window,
    progress: Mapping[str, Status],
    colors: Mapping[str, int],
) -> None:
    """Нарисовать экран ОБНОВИТЬ.

    На нем 8 широких баров по 7 символов. Они не являются историей: цвет
    показывает результат текущей проверки, а серый UNKNOWN означает, что сервис
    еще не проверен или его статус нельзя определить из-за зависимости.
    """

    stdscr.erase()
    box_width = 81
    x = (TOTAL_WIDTH - box_width) // 2
    top = "╭" + ("─────────┬" * 7) + "─────────╮"
    bottom = "╰" + ("─────────┴" * 7) + "─────────╯"

    addstr_safe(stdscr, 4, x, top, colors["frame"])
    cursor_x = x
    addstr_safe(stdscr, 5, cursor_x, "⎪", colors["frame"])
    cursor_x += 1

    for service in CHECK_ORDER:
        status = progress.get(service, Status.UNKNOWN)
        symbol = STATUS_SYMBOL[Status.UNKNOWN] if status == Status.NO_DATA else STATUS_SYMBOL[status]
        attr = attr_for_status(status, colors)
        addstr_safe(stdscr, 5, cursor_x, " ", colors["frame"])
        addstr_safe(stdscr, 5, cursor_x + 1, symbol * 7, attr)
        addstr_safe(stdscr, 5, cursor_x + 8, " ", colors["frame"])
        cursor_x += 9
        addstr_safe(stdscr, 5, cursor_x, "⎪", colors["frame"])
        cursor_x += 1

    addstr_safe(stdscr, 6, x, bottom, colors["frame"])
    addstr_safe(stdscr, 10, 0, footer_line(Screen.UPDATE), colors["label"])
    stdscr.refresh()


def init_colors() -> Mapping[str, int]:
    """Настроить цветовые пары curses."""

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_BLACK, -1)

    # COLOR_GRAY из ANSI-палитры соответствует "яркому черному". curses не на
    # всех терминалах умеет отдельный bright black, поэтому используем dim.
    return {
        "label": curses.color_pair(1),
        "yellow": curses.color_pair(2),
        "red": curses.color_pair(3),
        "gray": curses.color_pair(4) | curses.A_BOLD,
        "frame": curses.color_pair(4) | curses.A_BOLD,
    }


def next_screen(current: Screen, direction: int) -> Screen:
    """Получить следующий экран карусели.

    direction = 1 соответствует движению вперед:
    НЕДЕЛИ -> ДНИ -> ЧАСЫ -> ОБНОВИТЬ -> НЕДЕЛИ.

    direction = -1 соответствует движению назад:
    НЕДЕЛИ -> ОБНОВИТЬ -> ЧАСЫ -> ДНИ -> НЕДЕЛИ.
    """

    index = CAROUSEL.index(current)
    return CAROUSEL[(index + direction) % len(CAROUSEL)]


def perform_update(
    stdscr: curses.window,
    db: WatchdogDB,
    colors: Mapping[str, int],
    checkers: Mapping[str, Checker],
    step_delay_seconds: float,
) -> None:
    """Запустить обновление, показать прогресс и сохранить результат."""

    progress: MutableMapping[str, Status] = {}
    draw_update_screen(stdscr, progress, colors)

    def on_progress(_service: str, results: Mapping[str, Status]) -> None:
        progress.clear()
        progress.update(results)
        draw_update_screen(stdscr, progress, colors)

    results = run_service_checks(
        checkers,
        progress_callback=on_progress,
        step_delay_seconds=step_delay_seconds,
    )
    db.save_results(results)
    draw_update_screen(stdscr, results, colors)
    time.sleep(0.25)
    flush_input_buffer()


def flush_input_buffer() -> None:
    """Сбросить клавиши, накопленные во время заблокированного обновления.

    Пока открыт экран ОБНОВИТЬ, переходы по карусели заблокированы. curses при
    этом все равно складывает нажатые стрелки в очередь. Если ее не очистить, то
    после завершения проверки эти старые стрелки будут обработаны как новые и
    могут запустить несколько обновлений подряд.
    """

    try:
        curses.flushinp()
    except curses.error:
        pass


def next_auto_update_time(update_interval_seconds: int) -> float:
    """Вернуть monotonic-время следующего автообновления.

    Штатный интервал 10 минут привязываем к границам 10-минутной истории, чтобы
    экран ЧАСЫ закрашивал новый блок сразу после перехода времени. Пользователь
    все еще может передать другой --interval для отладки; такой интервал считаем
    буквально от текущего момента.
    """

    if update_interval_seconds == TEN_MINUTES:
        delay = seconds_until_next_history_bucket()
    else:
        delay = max(0, update_interval_seconds)
    return time.monotonic() + delay


def read_key(stdscr: curses.window) -> Optional[object]:
    """Прочитать одну клавишу без блокировки."""

    try:
        return stdscr.get_wch()
    except curses.error:
        return None


def open_touch_reader(touch_config: Optional[TouchConfig]) -> Optional[TouchSwipeReader]:
    """Открыть touch-reader, если он включен и доступен."""

    if touch_config is None or not touch_config.enabled:
        return None

    reader = TouchSwipeReader(
        touch_config.event_device,
        min_dx=touch_config.swipe_min_dx,
        max_dt_sec=touch_config.swipe_max_dt_sec,
        horizontal_ratio=touch_config.swipe_horizontal_ratio,
    )
    if reader.open():
        return reader
    return None


def read_swipe(touch_reader: Optional[TouchSwipeReader]) -> Optional[str]:
    if touch_reader is None:
        return None
    return touch_reader.consume_available()


def drain_swipes(touch_reader: Optional[TouchSwipeReader]) -> None:
    if touch_reader is not None:
        touch_reader.drain_available()


def run_tui(
    stdscr: curses.window,
    db: WatchdogDB,
    update_interval_seconds: int,
    touch_config: Optional[TouchConfig] = None,
    checkers: Optional[Mapping[str, Checker]] = None,
) -> None:
    """Главный цикл TUI."""

    if checkers is None:
        raise ValueError("run_tui requires configured checkers")

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    colors = init_colors()
    touch_reader = open_touch_reader(touch_config)

    current_screen = START_SCREEN
    next_auto_update_at = next_auto_update_time(update_interval_seconds)

    try:
        while True:
            if time.monotonic() >= next_auto_update_at:
                perform_update(stdscr, db, colors, checkers, DEFAULT_UPDATE_STEP_DELAY_SECONDS)
                drain_swipes(touch_reader)
                current_screen = Screen.HOURS
                next_auto_update_at = next_auto_update_time(update_interval_seconds)

            if current_screen == Screen.UPDATE:
                perform_update(stdscr, db, colors, checkers, DEFAULT_UPDATE_STEP_DELAY_SECONDS)
                drain_swipes(touch_reader)
                current_screen = Screen.HOURS
                next_auto_update_at = next_auto_update_time(update_interval_seconds)
            else:
                draw_history_screen(stdscr, db, current_screen, colors)

            key = read_key(stdscr)
            swipe = read_swipe(touch_reader)

            if key in ("q", "Q", "й", "Й"):
                break
            if key in (curses.KEY_RIGHT, "l", "L", "д", "Д") or swipe == SWIPE_LEFT:
                current_screen = next_screen(current_screen, 1)
            elif key in (curses.KEY_LEFT, "h", "H", "р", "Р") or swipe == SWIPE_RIGHT:
                current_screen = next_screen(current_screen, -1)
            elif key in ("\n", "\r", " "):
                # Пробел/Enter - быстрый ручной запуск обновления без листания.
                current_screen = Screen.UPDATE

            time.sleep(0.08)
    finally:
        if touch_reader is not None:
            touch_reader.close()
