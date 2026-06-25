"""Entrypoint: wire up config, state, store, MQTT bridge, control loop, and the
web panel, then serve.

    python -m master_controller [--config config.json] [--db history.db]

Runs as a single process: paho-mqtt's network loop and the control loop each run
on their own background threads; Flask serves on the main thread.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from .config import Config
from .controller import Controller
from .mqtt_client import MqttBridge
from .state import State
from .store import Store
from .web import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="master_controller")
    parser.add_argument("--config", default="config.json", help="path to config JSON")
    parser.add_argument("--db", default="history.db", help="path to SQLite history DB")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("master_controller")

    config = Config(args.config)
    state = State()
    store = Store(args.db)
    mqtt = MqttBridge(config, state, store)
    controller = Controller(config, state, mqtt)

    mqtt.start()
    controller.start()

    def shutdown(*_):
        log.info("shutting down")
        controller.stop()
        mqtt.stop()
        store.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    web_cfg = config.get("web")
    app = create_app(config, state, store, mqtt, controller)
    log.info("Web panel on http://%s:%s", web_cfg["host"], web_cfg["port"])
    # threaded=True so concurrent API calls don't block each other; the control
    # loop and MQTT run on their own threads regardless.
    app.run(host=web_cfg["host"], port=web_cfg["port"], threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
