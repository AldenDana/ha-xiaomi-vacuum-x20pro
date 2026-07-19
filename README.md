# Xiaomi Robot Vacuum X20 Pro — Room Cleaning for Home Assistant

[![Validate](https://github.com/AldenDana/ha-xiaomi-vacuum-x20pro/actions/workflows/validate.yml/badge.svg)](https://github.com/AldenDana/ha-xiaomi-vacuum-x20pro/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Working room-by-room cleaning — **including per-room custom settings** (suction, mop water, mode, passes) — for the **Xiaomi Robot Vacuum X20 Pro** (`xiaomi.vacuum.d102gl`) in Home Assistant.

No dedicated integration supports this model: it is Xiaomi in-house firmware (not Dreame-based, so [dreame-vacuum](https://github.com/Tasshack/dreame-vacuum/issues/862) can't help), the official [ha_xiaomi_home](https://github.com/XiaoMi/ha_xiaomi_home/discussions/109) doesn't expose room IDs, and the [map extractor](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor/issues/558) can't parse its maps. This project fills the gap with a thin, reliable layer on top of [al-one/hass-xiaomi-miot](https://github.com/al-one/hass-xiaomi-miot) (Xiaomi Miot Auto), based on live-tested MIoT calls. The full protocol findings are in [docs/METHOD.md](docs/METHOD.md).


## Technical Highlights

This project demonstrates practical reverse-engineering and Home Assistant automation work:

- Custom Home Assistant services layered on top of Xiaomi Miot Auto for a model with incomplete public integration support
- Live-verified MIoT action mapping for room cleaning, per-room settings, app-saved presets, and station actions
- Command acceptance verification with retry logic, because Xiaomi cloud acknowledgements do not always mean the robot executed the command
- Defensive parameter construction to avoid Home Assistant/YAML coercion bugs that can send invalid room-cleaning commands
- HACS-compatible packaging, script blueprints, and validation workflow for distribution

---

## What you get

- `xiaomi_x20pro.clean_rooms` — clean one or more rooms, optionally with per-room settings, with **acceptance verification and automatic retry** (the Xiaomi cloud often acks commands the robot never executes — see [docs/METHOD.md](docs/METHOD.md#cloud-ack--executed)).
- `xiaomi_x20pro.set_room_config` — store per-room settings without starting a clean (works even while the robot is in deep sleep; never moves it).
- `xiaomi_x20pro.start_preset` — start an **app-saved custom cleanup** (zones/rooms with their own settings). This is the reliable way to get zone cleaning: raw zone coordinates via `start-zone-sweep` are rejected in ways nobody has cracked yet ([details](docs/METHOD.md#zone-cleaning)), but presets saved in the Xiaomi Home app replay perfectly.
- Station controls: `start_mop_wash` / `stop_mop_wash`, `start_dry` / `stop_dry`, `start_dust_collection` — direct actions for the wash-dry station.
- `xiaomi_x20pro.start_preset_local` — start an app-saved cleanup over the **local network** (no cloud): works from any power state including deep sleep, with real verification. Uses the piid-tagged action format this firmware requires — see [docs/METHOD.md](docs/METHOD.md) for the discovery that bare-value payloads are silently ignored.
- Standalone [script blueprints](blueprints/script/) if you prefer pure YAML over a custom component.

## Requirements

- [Xiaomi Miot Auto](https://github.com/al-one/hass-xiaomi-miot) (HACS) with your X20 Pro added in **cloud mode** (the robot ignores local commands for these actions).

## Install

### Option A — HACS (custom repository)

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/AldenDana/ha-xiaomi-vacuum-x20pro` as type **Integration**.
2. Install **Xiaomi Vacuum X20 Pro Room Clean**.
3. Add this line to `configuration.yaml` and restart Home Assistant:

```yaml
xiaomi_x20pro:
```

### Option B — manual

Copy `custom_components/xiaomi_x20pro/` into your `config/custom_components/`, add `xiaomi_x20pro:` to `configuration.yaml`, restart.

### Option C — blueprints only

Import the [script blueprints](blueprints/script/) (Settings → Automations & Scenes → Blueprints → Import) and skip the integration entirely.

## Find your room IDs first

Room IDs live in the vacuum entity's `vacuum.room_information` attribute:

```
{"rooms":[{"id":6,"name":""},{"id":8,"name":""},...],"map_uid":8}
```

Two traps, both learned the hard way:

- **IDs change every time the map is remade.** After a remap, every automation with hardcoded IDs silently cleans the wrong rooms.
- **The room numbers shown in the Xiaomi Home app do NOT match these IDs.** The only reliable mapping method: clean one ID at a time and watch where the robot goes.

## Usage

```yaml
# Clean the kitchen (room 8) quietly with one pass:
action: xiaomi_x20pro.clean_rooms
data:
  entity_id: vacuum.xiaomi_d102gl_xxxx_robot_cleaner
  rooms: [8]
  fan_level: 1      # 1=Silent 2=Basic 3=Strong 4=Full Speed
  water_level: 1    # 0=Off 1=Low 2=Medium 3=High
  clean_mode: 3     # 1=Sweep 2=Mop 3=Sweep+Mop 4=Sweep then Mop
  clean_times: 1

# Two rooms with their stored per-room defaults:
action: xiaomi_x20pro.clean_rooms
data:
  entity_id: vacuum.xiaomi_d102gl_xxxx_robot_cleaner
  rooms: [8, 13]

# Pre-configure a room without cleaning (safe anytime, even in deep sleep):
action: xiaomi_x20pro.set_room_config
data:
  entity_id: vacuum.xiaomi_d102gl_xxxx_robot_cleaner
  rooms: [8]
  fan_level: 1

# Start an app-saved custom cleanup (e.g. a zone around the dining table):
action: xiaomi_x20pro.start_preset
data:
  entity_id: vacuum.xiaomi_d102gl_xxxx_robot_cleaner
  preset: 1
```

### Zone cleaning via presets

1. In the Xiaomi Home app, create and **save a custom cleanup** covering your zone (with the settings you want).
2. Read the vacuum's stored presets with `xiaomi_miot.get_properties` (`siid: 2, piid: 42`). You get something like:
   `{"user_labels":[{"id":1593689101,"name":"Dining table","v":1,...}]}`
3. Use the **small `v` value** as `preset` — **not** the long `id`; the long id gets a cloud ack but the robot silently ignores it.

`clean_rooms` polls `vacuum.current_cleaning_config` after each start command and re-fires (default: 5 retries, 25 s apart) until the robot reports the requested rooms — this rides out the post-clean station mop-wash and the dock's battery-cycling window that silently swallow commands.


## Security Notes

- This integration does not store Xiaomi credentials; authentication and device access remain handled by Xiaomi Miot Auto.
- Service examples use placeholder entity IDs and room IDs; users should map their own rooms locally.
- Raw map samples under `docs/map-samples/` are included only as protocol-research artifacts and are documented as encrypted/opaque data.
- The implementation avoids logging sensitive service payloads beyond normal Home Assistant service-call context.

---

## Known gotchas (see [docs/METHOD.md](docs/METHOD.md) for the full list)

- Cloud session death: if the entity goes to `unknown` for a long period, only a Home Assistant restart recovers it (`xiaomi_miot` has `supports_unload: false`).
- Never template `params` into `xiaomi_miot.call_action` as a bare comma string — HA coerces it into an int list and the robot can end up in a stuck state ([hass-xiaomi-miot#2735](https://github.com/al-one/hass-xiaomi-miot/issues/2735)). This integration builds params in Python to avoid the issue.
- `send_command app_segment_clean` fails silently in cloud mode; `current_cleaning_config` is read-only.

## Credits

- [al-one/hass-xiaomi-miot](https://github.com/al-one/hass-xiaomi-miot) does all the heavy lifting.
- The per-room `room_attrs` format was first confirmed on the sibling S20+ (`xiaomi.vacuum.b108gl`) in [this HA community thread](https://community.home-assistant.io/t/support-for-xiaomi-vacuum-s20-to-xiaomi-miio-integration/770596?page=2); action IDs translated for the d102gl from its [MIoT spec](https://home.miot-spec.com/spec/xiaomi.vacuum.d102gl) and live-verified.

Tested on firmware `0.0.23`, Home Assistant 2026.6, xiaomi_miot cloud mode. PRs and reports for other `xiaomi.vacuum.*` models welcome — the S20/S20+/X20/X20+ family likely works with different aiids (S20+: config=aiid 10, start=aiid 13).
