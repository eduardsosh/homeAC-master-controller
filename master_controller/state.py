"""Shared, thread-safe view of the live system state.

Written by the MQTT callback thread (incoming messages) and the control loop
(decisions/commands); read by everyone, including Flask request threads.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class State:
    def __init__(self):
        self._lock = threading.Lock()

        # Latest Aranet reading and when we received it (monotonic + wall clock).
        self.sensor: dict[str, Any] | None = None
        self.sensor_mono: float = 0.0
        self.sensor_ts: float = 0.0

        # Latest believed AC state (from ac/status).
        self.ac: dict[str, Any] | None = None

        # Retained liveness, seeded from the brokers' retained topics on connect.
        self.aranet_lwt: str | None = None
        self.aranet_avail: str | None = None
        self.ac_lwt: str | None = None

        # What the controller last commanded, and why it's doing what it's doing.
        self.last_command: dict[str, Any] | None = None
        self.last_command_mono: float = 0.0
        self.control_reason: str = "starting up"

    def update_sensor(self, reading: dict[str, Any]) -> None:
        with self._lock:
            self.sensor = reading
            self.sensor_mono = time.monotonic()
            self.sensor_ts = time.time()

    def update_ac(self, status: dict[str, Any]) -> None:
        with self._lock:
            self.ac = status

    def set_liveness(self, which: str, value: str) -> None:
        with self._lock:
            setattr(self, which, value)

    def note_command(self, command: dict[str, Any]) -> None:
        with self._lock:
            self.last_command = command
            self.last_command_mono = time.monotonic()

    def set_reason(self, reason: str) -> None:
        with self._lock:
            self.control_reason = reason

    def sensor_age_s(self) -> float | None:
        with self._lock:
            if self.sensor is None:
                return None
            return time.monotonic() - self.sensor_mono

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            age = None if self.sensor is None else time.monotonic() - self.sensor_mono
            return {
                "sensor": self.sensor,
                "sensor_age_s": age,
                "sensor_ts": self.sensor_ts,
                "ac": self.ac,
                "aranet_lwt": self.aranet_lwt,
                "aranet_avail": self.aranet_avail,
                "ac_lwt": self.ac_lwt,
                "last_command": self.last_command,
                "control_reason": self.control_reason,
            }
