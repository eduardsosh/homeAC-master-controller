"""The control loop: temperature regulation + safety failover.

Runs on its own thread, waking every ``control_interval_s``. Each tick it reads
the live sensor state and config, computes the desired AC state, and publishes a
command to ``ac/command`` *only when that desired state changes*.

Why "only on change": the ac_turn_on ESP32 fires the IR blaster on every command
it receives (it just suppresses the power toggle when already in the desired
state). Re-sending an unchanged command every tick would needlessly re-blast IR,
so we dedupe here.

Regulation uses a deadband (hysteresis) rather than switching on the exact
setpoint: the Aranet samples coarsely (~10 min) and the AC is slow, so a band
around the setpoint stops the AC flapping on/off.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .config import Config
from .mqtt_client import MqttBridge
from .state import State

log = logging.getLogger(__name__)


class Controller:
    def __init__(self, config: Config, state: State, mqtt: MqttBridge):
        self._config = config
        self._state = state
        self._mqtt = mqtt
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Desired power carried across ticks so the deadband has memory.
        self._desired_power = False
        # Last command we actually published, for change detection.
        self._last_sent: dict[str, Any] | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="controller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # never let the control loop die
                log.exception("control tick failed")
            interval = self._config.get("control")["control_interval_s"]
            self._stop.wait(timeout=max(1, interval))

    def tick(self) -> None:
        ctrl = self._config.get("control")
        snap = self._state.snapshot()

        if not ctrl["enabled"]:
            self._state.set_reason("automatic control disabled (manual mode)")
            return

        # --- Failover: AC unreachable -> can't command, don't try. ----------
        if snap["ac_lwt"] == "offline":
            self._state.set_reason("AC ESP32 offline (ac/lwt) — not commanding")
            return

        # --- Failover: blind on temperature -> fail safe (command AC off). --
        sensor_dead = (
            snap["sensor"] is None
            or snap["aranet_lwt"] == "offline"
            or snap["aranet_avail"] == "offline"
            or (snap["sensor_age_s"] is not None and snap["sensor_age_s"] > ctrl["sensor_stale_s"])
        )
        if sensor_dead:
            self._desired_power = False
            self._command(power=False, ctrl=ctrl, reason="sensor data unavailable/stale — failing safe (AC off)")
            return

        # --- Normal regulation with hysteresis. -----------------------------
        temp = snap["sensor"].get("temperature")
        if temp is None:
            self._state.set_reason("sensor reading has no temperature field")
            return

        setpoint = ctrl["setpoint_c"]
        band = ctrl["deadband_c"]
        mode = ctrl["mode"]

        if mode == "heat":
            if temp <= setpoint - band:
                self._desired_power = True
            elif temp >= setpoint + band:
                self._desired_power = False
        else:  # "cool" (and dry/fan default to cooling-style logic)
            if temp >= setpoint + band:
                self._desired_power = True
            elif temp <= setpoint - band:
                self._desired_power = False
        # In the deadband: keep the previous desired_power (no change).

        reason = (
            f"{temp:.1f}°C vs setpoint {setpoint:.1f}±{band:.1f} "
            f"({mode}) -> AC {'ON' if self._desired_power else 'OFF'}"
        )
        self._command(power=self._desired_power, ctrl=ctrl, reason=reason)

    def _command(self, power: bool, ctrl: dict[str, Any], reason: str) -> None:
        """Build the desired command and publish only if it changed."""
        command: dict[str, Any] = {"power": power}
        if power:  # only assert mode/fan/temp when we're turning/keeping it on
            command["mode"] = ctrl["mode"]
            command["fan"] = ctrl["fan"]
            command["temp"] = int(ctrl["ac_temp"])

        self._state.set_reason(reason)
        if command != self._last_sent:
            self._mqtt.send_ac_command(command)
            self._last_sent = command

    def force_resend(self) -> None:
        """Forget the dedupe cache so the next tick re-publishes (e.g. after a
        manual override, to let automatic control reassert control state)."""
        self._last_sent = None
