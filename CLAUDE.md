# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`master_controller` is a Python service that runs on the broker host (or any always-on
machine on the LAN) and **closes the control loop** over MQTT between two ESP32 modules:

- **`aranet_esp32`** — publishes room sensor readings (temperature, humidity, CO2, …) to
  `aranet/status`.
- **`ac_turn_on`** — controls a Panasonic AC via IR. Subscribes to `ac/command`, publishes
  believed state to `ac/status`.

The master controller subscribes to the sensor stream, decides what the AC should do, and
publishes commands to `ac/command` — turning two independent devices into a thermostat.

Its two jobs (chosen scope):
1. **Temperature regulation** — hold a target setpoint by commanding the AC from `aranet/status`
   temperature readings.
2. **Safety / failover** — watch device liveness (`aranet/lwt`, `ac/lwt`) and fail safe when a
   device drops off the broker.

This repo is the *brain*; it does no IR or BLE itself. All hardware lives in the two ESP32
sketches. **The MQTT topic/JSON contract below is the integration boundary — treat it as fixed
unless the corresponding ESP32 sketch is changed in lockstep.**

## Runtime & dependencies

- **Python 3** with [`paho-mqtt`](https://pypi.org/project/paho-mqtt/) (MQTT client) and
  **Flask** (web panel). JSON and SQLite via the stdlib. Deps pinned in `requirements.txt`.
- Target deployment: a Windows local server, run as a service (NSSM / Task Scheduler — see
  `README.md`). Code is cross-platform; the only OS-specific bit is `SIGTERM` handling in
  `__main__.py`, which simply no-ops on Windows (the retained LWT still reports us offline).

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m master_controller --config config.json --db history.db
```

Single process: paho's network loop and the control loop each run on a background thread;
Flask serves on the main thread. Shared state is guarded by locks in `state.py` / `config.py`.

## Code layout

| File | Responsibility |
|---|---|
| `master_controller/__main__.py` | Entrypoint — wires config/state/store/mqtt/controller/web, runs Flask + threads |
| `master_controller/config.py` | File-backed, thread-safe config; only `control.*` is live-editable via the panel |
| `master_controller/state.py` | Thread-safe snapshot of live system state (sensor, AC, liveness, last command) |
| `master_controller/store.py` | SQLite history (one row per reading) for the statistics charts |
| `master_controller/mqtt_client.py` | paho-mqtt bridge: own retained LWT, subscribe + route messages, publish commands |
| `master_controller/controller.py` | The control loop — temp regulation (hysteresis) + safety failover |
| `master_controller/web.py` | Flask panel + JSON API (`/api/status`, `/api/history`, `/api/config`, `/api/ac/command`) |
| `master_controller/templates/index.html` | Panel UI (status, Chart.js stats, config form, manual control) |

**Config:** all settings live in one JSON file (`config.json`, seeded from `config.example.json`
on first run), mirroring how each ESP32 sketch keeps its constants atop `networking.ino`. Do not
scatter the broker IP through the code. The panel writes back only `control.*` keys; MQTT/topic/
web settings require a restart.

**Command dedupe (important):** the `ac_turn_on` ESP32 fires the IR blaster on *every* command it
receives (it only suppresses the redundant power *toggle*). So `controller.py` publishes to
`ac/command` **only when the desired command changes** — never re-send an identical command each
tick. If you add fields to the command, keep that change-detection intact.

## The shared MQTT environment

A single Mosquitto broker at **`192.168.0.213:1883`** is shared by all modules. Each device
uses a distinct client id (`ESP32-AC-CTRL`, `ESP32-ARANET`); the master controller must use its
own (e.g. `master-controller`). Two brokers conventions to follow:

- **Availability via retained LWT.** Every module registers an MQTT Last Will so the broker
  publishes `offline` (retained) to its `*/lwt` topic if its TCP session dies, and publishes
  `online` itself on connect. The master controller **must do the same** for its own liveness
  (suggested topic `master/lwt`) so the rest of the system can detect *it* dropping.
- **Retained state topics.** Status/availability topics are published *retained*, so a late
  subscriber immediately sees the last known value. Subscribe and you'll get current state
  without waiting for the next update.

### Topics this controller uses

| Topic | Dir (from controller) | Retained | Payload |
|---|---|---|---|
| `aranet/status` | subscribe | no | sensor readings JSON (below) |
| `aranet/availability` | subscribe | yes | `online`/`offline` — sensor health |
| `aranet/lwt` | subscribe | yes | `online`/`offline` — aranet ESP32 liveness |
| `aranet/command` | publish | no | `{"read": true}` to force an out-of-band read |
| `ac/command` | publish | no | AC command JSON (below) |
| `ac/status` | subscribe | yes | AC believed state JSON (below) |
| `ac/lwt` | subscribe | yes | `online`/`offline` — AC ESP32 liveness |
| `master/lwt` | publish (LWT) | yes | `online`/`offline` — this controller's liveness |

### Payload formats (do not drift from these)

**`aranet/status`** (sensor → controller), published on each sensor read (~every 10 min by
default; the Aranet sets its own interval):
```json
{ "co2": 612, "temperature": 21.5, "humidity": 45,
  "pressure": 1013.2, "battery": 90, "interval": 300, "ago": 42 }
```

**`ac/command`** (controller → AC). All fields optional — send only what changes. `power` is the
**desired state**, not a toggle: the ESP32 tracks the AC's believed power and sends an IR toggle
only when `power` differs from that belief. So sending `power: true` repeatedly is safe (idempotent).
```json
{ "power": true, "temp": 22, "mode": "cool", "fan": "auto" }
```
Accepted values: `power` `true`/`false`; `temp` integer °C; `mode` `cool|heat|dry|fan|auto`;
`fan` `auto|min|low|med|high|max`. A `{"resync": true|false}` field corrects the ESP32's power
belief **without** firing IR (used after operating the AC by hand — the controller generally
won't need it).

**`ac/status`** (AC → controller), retained:
```json
{ "power": true, "temp": 22, "mode": "cool", "fan": "auto", "power_toggled": true }
```
`power` = believed on/off state; `power_toggled` = whether *that* command actually sent a toggle.

## Control logic

### Temperature regulation
- Maintain a target setpoint. On each `aranet/status` message, compare `temperature` to the
  setpoint and command the AC accordingly via `ac/command`.
- **Use hysteresis / a deadband**, not bang-bang on the exact setpoint — the Aranet only samples
  ~every 10 min and the AC is slow, so a deadband (e.g. ±0.5–1 °C) avoids needless mode flapping.
  Don't re-issue an identical command every reading; `ac/command` is idempotent but spamming IR
  is pointless — only publish when the desired AC state actually changes.
- The Aranet's sample interval is coarse. If you need a fresher reading for a decision, publish
  `{"read": true}` to `aranet/command` to force an out-of-band sensor read.

### Safety / failover
The master controller is a watchdog as well as a regulator. Define explicit behaviour for these
edges and keep it simple:
- **Sensor gone** (`aranet/lwt` = `offline`, or `aranet/availability` = `offline`, or `aranet/status`
  stale for several intervals): you are flying blind on temperature. Fail safe — prefer commanding
  the AC **off** over holding a setpoint on stale data. Decide and document the staleness timeout.
- **AC gone** (`ac/lwt` = `offline`): the AC ESP32 is unreachable; commands won't land. Note that
  `ac_turn_on` already self-protects — on its own link loss it locally toggles the AC off. The
  controller should stop issuing commands and surface the condition rather than queue blindly.
- **Controller gone**: covered by registering `master/lwt` as an MQTT Last Will, so others can see
  the brain is down.

Liveness is *retained*, so on (re)connect you immediately learn each device's last known state —
seed your failover state machine from those retained values rather than assuming everything is up.

## Conventions to keep consistent with the ESP32 modules

- Config constants (broker, port, client id, topics, setpoint, deadband, timeouts) grouped in one
  place, like the top of each `networking.ino`.
- Retained for state/availability; non-retained for transient commands/readings.
- Own client id + own retained-LWT liveness topic.
- Match the existing topic-naming scheme (`<device>/<purpose>`).

## Related projects (siblings under `~/Arduino/`)
- `../ac_turn_on/` — AC IR controller ESP32 firmware + `USAGE.md` (the canonical `ac/*` reference).
- `../aranet_esp32/` — Aranet4 BLE→MQTT sensor bridge firmware (the canonical `aranet/*` reference).

When the MQTT contract here and a sketch there disagree, the **sketch is the source of truth** for
its own topics — read it (and its CLAUDE.md) before changing payload handling on this side.
