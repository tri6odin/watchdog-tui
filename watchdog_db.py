"""
SQLite-слой Watchdog.

В базе хранится только базовая 10-минутная история. Более крупные интервалы для
экранов ДНИ и НЕДЕЛИ собираются при чтении.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from watchdog_core import (
    BAR_WIDTH,
    SCREEN_CELL_SECONDS,
    SERVICES,
    TEN_MINUTES,
    Screen,
    Status,
    aggregate_statuses,
    align_timestamp,
)


class WatchdogDB:
    """Тонкая обертка над SQLite.

    История хранится в базовом шаге 10 минут. Несколько проверок внутри одной
    10-минутной корзины сливаются по приоритету FAIL > UNKNOWN > OK. Поэтому
    ручная повторная проверка не может стереть уже пойманный сбой в интервале.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS history (
                bucket_start INTEGER NOT NULL,
                checked_at INTEGER NOT NULL,
                service TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (bucket_start, service)
            );

            CREATE INDEX IF NOT EXISTS idx_history_service_time
            ON history (service, bucket_start);
            """
        )
        self.conn.commit()

    def save_results(self, results: Mapping[str, Status], checked_at: Optional[float] = None) -> None:
        """Сохранить результат проверки всех сервисов."""

        now = time.time() if checked_at is None else checked_at
        bucket_start = align_timestamp(now, TEN_MINUTES)
        with self.conn:
            for service, status in results.items():
                self._save_service_result(bucket_start, int(now), service, status)

    def _save_service_result(
        self,
        bucket_start: int,
        checked_at: int,
        service: str,
        status: Status,
    ) -> None:
        """Сохранить статус сервиса, не затирая более серьезный статус ячейки."""

        existing = self.conn.execute(
            """
            SELECT status
            FROM history
            WHERE bucket_start = ? AND service = ?
            """,
            (bucket_start, service),
        ).fetchone()
        statuses = [status] if existing is None else [Status(existing["status"]), status]
        merged_status = aggregate_statuses(statuses)

        self.conn.execute(
            """
            INSERT INTO history (bucket_start, checked_at, service, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(bucket_start, service) DO UPDATE SET
                checked_at = excluded.checked_at,
                status = excluded.status
            """,
            (bucket_start, checked_at, service, merged_status.value),
        )

    def load_history(self, screen: Screen, now: Optional[float] = None) -> Dict[str, List[Status]]:
        """Загрузить 43 ячейки истории для каждого сервиса на выбранный экран."""

        if screen not in SCREEN_CELL_SECONDS:
            raise ValueError(f"Экран {screen.value} не является историческим")

        first_cell_start, range_end, cell_seconds = self._visible_range(screen, now)

        # Сначала собираем все 10-минутные записи одним запросом. Это дешевле и
        # проще для Raspberry Pi, чем делать 8 * 43 отдельных SELECT.
        grouped: Dict[str, List[List[Status]]] = {
            service: [[] for _ in range(BAR_WIDTH)] for service in SERVICES
        }
        rows = self.conn.execute(
            """
            SELECT bucket_start, service, status
            FROM history
            WHERE bucket_start >= ? AND bucket_start < ?
            ORDER BY bucket_start ASC
            """,
            (first_cell_start, range_end),
        )

        for row in rows:
            service = row["service"]
            if service not in grouped:
                continue
            index = (int(row["bucket_start"]) - first_cell_start) // cell_seconds
            if 0 <= index < BAR_WIDTH:
                grouped[service][index].append(Status(row["status"]))

        return {
            service: [aggregate_statuses(cell) for cell in cells]
            for service, cells in grouped.items()
        }

    def last_checked_at(self, screen: Screen, now: Optional[float] = None) -> Optional[int]:
        """Вернуть последнее время проверки внутри видимого диапазона экрана."""

        if screen not in SCREEN_CELL_SECONDS:
            raise ValueError(f"Экран {screen.value} не является историческим")

        first_cell_start, range_end, _cell_seconds = self._visible_range(screen, now)
        row = self.conn.execute(
            """
            SELECT MAX(checked_at) AS last_checked_at
            FROM history
            WHERE bucket_start >= ? AND bucket_start < ?
            """,
            (first_cell_start, range_end),
        ).fetchone()
        if row is None or row["last_checked_at"] is None:
            return None
        return int(row["last_checked_at"])

    def _visible_range(self, screen: Screen, now: Optional[float]) -> tuple[int, int, int]:
        """Вернуть начало, конец и размер ячейки видимого диапазона экрана."""

        current_time = time.time() if now is None else now
        cell_seconds = SCREEN_CELL_SECONDS[screen]
        last_cell_start = align_timestamp(current_time, cell_seconds)
        first_cell_start = last_cell_start - (BAR_WIDTH - 1) * cell_seconds
        range_end = last_cell_start + cell_seconds
        return first_cell_start, range_end, cell_seconds

    def close(self) -> None:
        self.conn.close()
