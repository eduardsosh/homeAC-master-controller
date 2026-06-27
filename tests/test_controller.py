"""Unit tests for the control loop's sun-window + curfew gate.

Broker-free and dependency-free (stdlib ``unittest``), in the same spirit as
``test_smoke.py``: a real Config/State with a mocked MQTT bridge, with the wall
clock patched so we can drive the time-of-day gate deterministically.
"""

import tempfile
import unittest
from datetime import time as dt_time
from pathlib import Path
from unittest.mock import MagicMock, patch

from master_controller import controller as controller_mod
from master_controller.config import Config
from master_controller.controller import Controller
from master_controller.state import State


class TestSunCurfewGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self._tmp.name) / "config.json")
        # Enabled, cooling, with a hot room so regulation *wants* the AC on.
        self.config.update_control({
            "enabled": True,
            "mode": "cool",
            "setpoint_c": 22.0,
            "deadband_c": 0.5,
            "sun_window_start": "10:00",
            "sun_window_end": "18:00",
            "off_after": "21:00",
        })
        self.state = State()
        self.state.update_sensor({"temperature": 30.0})  # well above setpoint
        self.state.set_liveness("ac_lwt", "online")
        self.state.set_liveness("aranet_lwt", "online")
        self.state.set_liveness("aranet_avail", "online")
        self.mqtt = MagicMock()
        self.controller = Controller(self.config, self.state, self.mqtt)

    def tearDown(self):
        self._tmp.cleanup()

    def _tick_at(self, hh: int, mm: int = 0):
        with patch.object(controller_mod, "datetime") as dt:
            dt.now.return_value.time.return_value = dt_time(hh, mm)
            self.controller.tick()

    def _last_power(self):
        self.assertTrue(self.mqtt.send_ac_command.called, "no command was sent")
        return self.mqtt.send_ac_command.call_args.args[0]["power"]

    def test_runs_inside_sun_window(self):
        self._tick_at(14, 0)
        self.assertTrue(self._last_power())

    def test_off_before_sun_window(self):
        self._tick_at(8, 0)
        self.assertFalse(self._last_power())

    def test_off_after_sun_window(self):
        self._tick_at(19, 30)
        self.assertFalse(self._last_power())

    def test_curfew_forces_off_at_2100(self):
        self._tick_at(21, 0)
        self.assertFalse(self._last_power())

    def test_curfew_forces_off_after_2100(self):
        self._tick_at(22, 15)
        self.assertFalse(self._last_power())

    def test_curfew_overrides_even_if_sun_window_extends_late(self):
        # Summer sun window past curfew: curfew still wins.
        self.config.update_control({"sun_window_end": "23:00"})
        self._tick_at(21, 30)
        self.assertFalse(self._last_power())


if __name__ == "__main__":
    unittest.main()
