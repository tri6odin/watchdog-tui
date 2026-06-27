#!/usr/bin/env python3
"""
Точка входа Watchdog TUI.

Основная логика разнесена по модулям:
- watchdog_core.py - сервисы, статусы, проверки и агрегация;
- watchdog_db.py - SQLite-история;
- watchdog_ui.py - curses-интерфейс.
"""

from __future__ import annotations

import argparse
import curses
import locale
import sys
from pathlib import Path

from watchdog_config import DEFAULT_CONFIG_PATH, ConfigError, load_watchdog_config
from watchdog_core import CHECK_ORDER, DEFAULT_UPDATE_INTERVAL_SECONDS, configure_checks, run_service_checks
from watchdog_db import WatchdogDB
from watchdog_ui import run_tui


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Домашний Watchdog TUI с SQLite-историей.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Путь к .conf файлу с адресами, таймаутами и секретами. По умолчанию: ./watchdog.conf",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("watchdog.sqlite3"),
        help="Путь к SQLite базе. По умолчанию: ./watchdog.sqlite3",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_UPDATE_INTERVAL_SECONDS,
        help="Интервал автообновления в секундах. По умолчанию: 600.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Один раз выполнить проверки, сохранить результат и выйти без TUI.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        configure_checks(load_watchdog_config(args.config))
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    db = WatchdogDB(args.db)
    try:
        if args.once:
            results = run_service_checks(step_delay_seconds=0)
            db.save_results(results)
            for service in CHECK_ORDER:
                print(f"{service:8s} {results[service].value}")
            return 0

        # Локаль важно включить до curses.wrapper/initscr, чтобы Python curses
        # корректно передавал в терминал Unicode-рамки, квадраты и индексы шкалы.
        locale.setlocale(locale.LC_ALL, "")
        curses.wrapper(run_tui, db, args.interval)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
