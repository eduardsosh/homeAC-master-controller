"""Persistent history for the statistics panel.

A tiny SQLite store (stdlib, zero-config, works fine on Windows). One row per
Aranet reading, tagged with the believed AC power and the active setpoint at the
time, so the panel can plot room temperature against what the AC was doing.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: str | Path):
        self._lock = threading.Lock()
        # check_same_thread=False: we serialise all access with our own lock, so
        # the connection can be shared across the MQTT/control/web threads.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                ts         REAL NOT NULL,
                temperature REAL,
                humidity   REAL,
                co2        INTEGER,
                pressure   REAL,
                battery    INTEGER,
                ac_power   INTEGER,
                setpoint   REAL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts)")
        self._conn.commit()

    def record(self, reading: dict[str, Any], ac_power: bool | None, setpoint: float | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO readings (ts, temperature, humidity, co2, pressure, "
                "battery, ac_power, setpoint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    reading.get("temperature"),
                    reading.get("humidity"),
                    reading.get("co2"),
                    reading.get("pressure"),
                    reading.get("battery"),
                    None if ac_power is None else int(bool(ac_power)),
                    setpoint,
                ),
            )
            self._conn.commit()

    def history(self, since_s: float = 24 * 3600, limit: int = 5000) -> list[dict[str, Any]]:
        """Return readings from the last ``since_s`` seconds, oldest first."""
        cutoff = time.time() - since_s
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM (SELECT * FROM readings WHERE ts >= ? "
                "ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, keep_s: float = 30 * 24 * 3600) -> None:
        """Drop readings older than ``keep_s`` (default 30 days)."""
        with self._lock:
            self._conn.execute("DELETE FROM readings WHERE ts < ?", (time.time() - keep_s,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
