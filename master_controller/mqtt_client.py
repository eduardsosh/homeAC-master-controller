"""MQTT wiring: connect with our own retained LWT, subscribe to the device
topics, route incoming messages into shared State + the history Store, and
expose helpers to command the AC and force a sensor read.

Follows the conventions of the ESP32 sketches: own client id, retained-LWT
liveness on ``master/lwt`` (online on connect / broker-published offline on
drop), and retained device state seeded on (re)connect via subscriptions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import paho.mqtt.client as mqtt

from .config import Config
from .state import State
from .store import Store

log = logging.getLogger(__name__)


class MqttBridge:
    def __init__(self, config: Config, state: State, store: Store):
        self._config = config
        self._state = state
        self._store = store

        mqtt_cfg = config.get("mqtt")
        self._lwt_topic = mqtt_cfg["lwt_topic"]
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=mqtt_cfg["client_id"],
            clean_session=True,
        )
        # Broker publishes this (retained) if our TCP session dies ungracefully.
        self._client.will_set(self._lwt_topic, "offline", qos=0, retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        cfg = self._config.get("mqtt")
        self._client.connect(cfg["host"], cfg["port"], keepalive=60)
        self._client.loop_start()  # background network thread

    def stop(self) -> None:
        try:
            self._client.publish(self._lwt_topic, "offline", qos=0, retain=True)
        finally:
            self._client.loop_stop()
            self._client.disconnect()

    # --- callbacks ---------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected")
        client.publish(self._lwt_topic, "online", qos=0, retain=True)
        topics = self._config.get("topics")
        # Subscribe in on_connect so we re-subscribe automatically after a reconnect.
        for key in (
            "aranet_status",
            "aranet_lwt",
            "aranet_availability",
            "ac_status",
            "ac_lwt",
        ):
            client.subscribe(topics[key])

    def _on_message(self, client, userdata, msg):
        topics = self._config.get("topics")
        payload = msg.payload.decode("utf-8", errors="replace")

        if msg.topic == topics["aranet_status"]:
            reading = self._parse_json(payload)
            if reading is None:
                return
            self._state.update_sensor(reading)
            ctrl = self._config.get("control")
            ac = self._state.snapshot()["ac"]
            ac_power = ac.get("power") if isinstance(ac, dict) else None
            self._store.record(reading, ac_power, ctrl["setpoint_c"])

        elif msg.topic == topics["ac_status"]:
            status = self._parse_json(payload)
            if status is not None:
                self._state.update_ac(status)

        elif msg.topic == topics["aranet_lwt"]:
            self._state.set_liveness("aranet_lwt", payload)
        elif msg.topic == topics["aranet_availability"]:
            self._state.set_liveness("aranet_avail", payload)
        elif msg.topic == topics["ac_lwt"]:
            self._state.set_liveness("ac_lwt", payload)

    @staticmethod
    def _parse_json(payload: str) -> dict[str, Any] | None:
        try:
            data = json.loads(payload)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            log.warning("Bad JSON payload: %r", payload)
            return None

    # --- outbound ----------------------------------------------------------
    def send_ac_command(self, command: dict[str, Any]) -> None:
        """Publish a command to ac/command (non-retained, like the ESP32 expects)."""
        topic = self._config.get("topics")["ac_command"]
        self._client.publish(topic, json.dumps(command), qos=0, retain=False)
        self._state.note_command(command)
        log.info("AC command -> %s", command)

    def request_sensor_read(self) -> None:
        """Force an out-of-band Aranet read via aranet/command."""
        topic = self._config.get("topics")["aranet_command"]
        self._client.publish(topic, json.dumps({"read": True}), qos=0, retain=False)
        log.info("Forced sensor read")
