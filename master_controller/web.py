"""Flask web panel: live status, statistics charts, config editing, and manual
AC control. Talks to the running controller/MQTT bridge via shared objects (no
separate process), so the UI reflects and drives the live service.
"""

from __future__ import annotations

import logging

from flask import Flask, jsonify, render_template, request

from .config import Config
from .controller import Controller
from .mqtt_client import MqttBridge
from .state import State
from .store import Store

log = logging.getLogger(__name__)


def create_app(config: Config, state: State, store: Store, mqtt: MqttBridge, controller: Controller) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    def api_status():
        return jsonify({"state": state.snapshot(), "control": config.get("control")})

    @app.get("/api/history")
    def api_history():
        hours = request.args.get("hours", default=24, type=float)
        return jsonify(store.history(since_s=hours * 3600))

    @app.get("/api/config")
    def api_get_config():
        return jsonify(config.snapshot())

    @app.post("/api/config")
    def api_set_config():
        changes = request.get_json(force=True, silent=True) or {}
        updated = config.update_control(changes)
        controller.force_resend()  # let the new settings take effect immediately
        return jsonify({"control": updated})

    @app.post("/api/ac/command")
    def api_ac_command():
        """Manual AC control. Publishes straight to ac/command. Note: if
        automatic control is enabled it may override this on the next tick —
        disable control first (set control.enabled=false) for sustained manual use."""
        command = request.get_json(force=True, silent=True) or {}
        mqtt.send_ac_command(command)
        controller.force_resend()
        return jsonify({"sent": command})

    @app.post("/api/sensor/read")
    def api_force_read():
        mqtt.request_sensor_read()
        return jsonify({"requested": True})

    return app
