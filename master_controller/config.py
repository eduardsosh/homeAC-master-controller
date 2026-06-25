"""Configuration loading and live editing.

Config lives in a single JSON file so the web panel can edit it at runtime. All
access goes through the thread-safe ``Config`` object: the MQTT callback thread,
the control loop, and Flask request threads all read it concurrently.
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

# Defaults mirror config.example.json. Anything missing from the on-disk file
# falls back to these, so a partial config (or a panel that only writes the keys
# it changed) still produces a complete, valid config.
DEFAULTS: dict[str, Any] = {
    "mqtt": {
        "host": "192.168.0.213",
        "port": 1883,
        "client_id": "master-controller",
        "lwt_topic": "master/lwt",
    },
    "topics": {
        "aranet_status": "aranet/status",
        "aranet_lwt": "aranet/lwt",
        "aranet_availability": "aranet/availability",
        "aranet_command": "aranet/command",
        "ac_command": "ac/command",
        "ac_status": "ac/status",
        "ac_lwt": "ac/lwt",
    },
    "control": {
        "enabled": True,
        "mode": "cool",
        "fan": "auto",
        "setpoint_c": 22.0,
        "deadband_c": 0.5,
        "ac_temp": 22,
        "control_interval_s": 30,
        "sensor_stale_s": 2400,
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8000,
    },
}

# Only these sections/keys may be edited via the web panel. MQTT/topic/web
# settings require a restart, so we don't expose them to live edits.
EDITABLE = {
    "control": {
        "enabled",
        "mode",
        "fan",
        "setpoint_c",
        "deadband_c",
        "ac_temp",
        "control_interval_s",
        "sensor_stale_s",
    }
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """Thread-safe, file-backed configuration."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULTS)
        if self._path.exists():
            with self._path.open(encoding="utf-8") as fh:
                self._data = _deep_merge(DEFAULTS, json.load(fh))
        else:
            self.save()  # materialise a complete config on first run

    def snapshot(self) -> dict[str, Any]:
        """Return a deep copy safe to read without holding the lock."""
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, section: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data[section])

    def update_control(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Apply panel edits to the ``control`` section, validate, persist.

        Unknown or non-editable keys are ignored. Returns the new control dict.
        """
        with self._lock:
            for key, value in changes.items():
                if key in EDITABLE["control"]:
                    self._data["control"][key] = _coerce_control(key, value)
            self.save_locked()
            return copy.deepcopy(self._data["control"])

    def save(self) -> None:
        with self._lock:
            self.save_locked()

    def save_locked(self) -> None:
        # Atomic write so a crash mid-save can't truncate the config file.
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)
        tmp.replace(self._path)


_FLOAT_KEYS = {"setpoint_c", "deadband_c"}
_INT_KEYS = {"ac_temp", "control_interval_s", "sensor_stale_s"}


def _coerce_control(key: str, value: Any) -> Any:
    """Coerce panel-supplied values to the right type (form posts arrive as strings)."""
    if key == "enabled":
        return value in (True, "true", "True", "on", 1, "1")
    if key in _FLOAT_KEYS:
        return float(value)
    if key in _INT_KEYS:
        return int(float(value))
    return str(value)
