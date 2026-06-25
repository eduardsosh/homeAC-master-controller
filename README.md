# master_controller

Python service that closes the control loop between the `aranet_esp32` CO2/temperature
sensor and the `ac_turn_on` IR AC controller over MQTT — a software thermostat with a web
panel for status, statistics, live config editing, and manual AC control.

See [CLAUDE.md](CLAUDE.md) for the MQTT contract and architecture.

## Run

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

copy config.example.json config.json   # (Windows)  — or: cp config.example.json config.json
python -m master_controller --config config.json --db history.db
```

Then open the panel at <http://localhost:8000> (or the LAN IP of the server).

- `config.json` is created from defaults on first run if missing, and is rewritten when you
  edit control settings in the panel. Only `control.*` settings are editable live; MQTT/topic/
  web settings need a restart.
- `history.db` is a SQLite file holding one row per sensor reading for the statistics charts.

## Run as a service on Windows

Two simple options:

1. **NSSM** (Non-Sucking Service Manager) — wrap the venv python:
   ```
   nssm install ACMaster "C:\path\to\.venv\Scripts\python.exe" "-m master_controller --config C:\path\to\config.json --db C:\path\to\history.db"
   nssm set ACMaster AppDirectory C:\path\to\master_controller
   nssm start ACMaster
   ```
2. **Task Scheduler** — create a task "At startup" running the same python + args, "Run whether
   user is logged on or not".

(Note: `SIGTERM` handling works on Linux; on Windows service stop, the process is terminated —
the broker's retained Last Will still flips `master/lwt` to `offline`, so liveness is correct.)

## Layout

| File | Responsibility |
|---|---|
| `master_controller/__main__.py` | Entrypoint — wires everything, runs Flask + background threads |
| `master_controller/config.py`   | File-backed, thread-safe config; live-editable `control.*` |
| `master_controller/state.py`    | Thread-safe snapshot of live system state |
| `master_controller/store.py`    | SQLite history for the statistics panel |
| `master_controller/mqtt_client.py` | paho-mqtt bridge: subscribe, route, publish commands |
| `master_controller/controller.py`  | Control loop: temp regulation + safety failover |
| `master_controller/web.py`       | Flask panel + JSON API |
| `master_controller/templates/index.html` | The panel UI |
