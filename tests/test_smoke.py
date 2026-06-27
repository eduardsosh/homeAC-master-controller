"""Deployment smoke test: prove the web app boots and the API responds.

Builds the real Flask app via ``create_app`` with real Config/State/Store and a
mocked MQTT bridge (so no broker is needed), then drives it through Flask's test
client. This exercises the bits that actually break a deploy — routing, JSON
serialisation, template rendering, config and DB wiring — without binding a port
or connecting to MQTT. Runs on the stdlib (``python -m unittest``), so CD needs
no extra dependency beyond the deployed venv.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from master_controller.config import Config
from master_controller.state import State
from master_controller.store import Store
from master_controller.web import create_app


class TestApiSmoke(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)

        self.config = Config(tmp / "config.json")
        self.state = State()
        self.store = Store(":memory:")
        self.mqtt = MagicMock()          # no broker in CI
        self.controller = MagicMock()    # no control thread

        app = create_app(self.config, self.state, self.store, self.mqtt, self.controller)
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_index_page_renders(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_status_endpoint(self):
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn("state", body)
        self.assertIn("control", body)

    def test_config_endpoint(self):
        resp = self.client.get("/api/config")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("control", resp.get_json())

    def test_history_endpoint(self):
        resp = self.client.get("/api/history")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_manual_command_endpoint(self):
        resp = self.client.post("/api/ac/command", json={"power": True})
        self.assertEqual(resp.status_code, 200)
        self.mqtt.send_ac_command.assert_called_once_with({"power": True})


if __name__ == "__main__":
    unittest.main()
