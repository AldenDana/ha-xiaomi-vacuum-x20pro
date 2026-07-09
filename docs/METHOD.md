# The X20 Pro room-cleaning protocol, documented

Everything below was live-verified in July 2026 on a `xiaomi.vacuum.d102gl` (firmware `0.0.23`) through `xiaomi_miot` (Xiaomi Miot Auto) in cloud mode, unless marked otherwise. As far as we know this is the only complete working documentation of room cleaning for this model.

## Relevant MIoT actions (siid 2)

From the [official spec](https://home.miot-spec.com/spec/xiaomi.vacuum.d102gl):

| aiid | name | input piid | verified |
|---|---|---|---|
| 12 | set-zone | 12 `zone-ids` (string) | – |
| 13 | **set-room-clean-configs** | 16 `room-information` (string) | ✅ |
| 16 | **start-vacuum-room-sweep** | 15 `vacuum-room-ids` (string) | ✅ |
| 37 | start-zone-sweep | 12 `zone-ids` | – |
| 38 | add-user-sweep-setting | 42 `user-define-sweep-cfg` (string) | – |
| 39 | del-user-sweep-setting | 43 `user-define-sweep-id` (uint16) | – |
| 40 | modify-user-sweep-setting | 42 | – |
| 42 | start-user-define-sweep | 43 `user-define-sweep-id` | – |

Property value maps (settable globally via `set_miot_property`, or per-room via aiid 13):

- Mode / fan (`piid 9`, per-room `fan_level`): 1=Silent 2=Basic 3=Strong 4=Full Speed
- Sweep type (`piid 4`, per-room `clean_mode`): 1=Sweep 2=Mop 3=Sweep+Mop 4=Sweep then Mop
- Water (`piid 10`, per-room `water_level`): 0=Off 1=Low 2=Medium 3=High
- Passes (`piid 8`, per-room `clean_times`): 1/2/3

## Start a room clean

```yaml
action: xiaomi_miot.call_action
target:
  entity_id: vacuum.xiaomi_d102gl_xxxx_robot_cleaner
data:
  siid: 2
  aiid: 16
  params: ["8"]        # single room — room ID as string
  # params: ["8,13"]   # multiple rooms — one comma-separated string
```

On acceptance the robot sets `current_cleaning_config: {"rooms":[8],"clean_mode":3}`, runs the station mop pre-wash (GoWash), cleans only those rooms, docks, and post-washes. Do **not** follow with `button.press start_sweep_mop` — that overrides the room selection with a full-house clean.

## Per-room custom settings (aiid 13)

`set-room-clean-configs` stores per-room settings that become that room's defaults. It never moves the robot, and the cloud accepts it even while the robot is in deep sleep:

```yaml
action: xiaomi_miot.call_action
target:
  entity_id: vacuum.xiaomi_d102gl_xxxx_robot_cleaner
data:
  siid: 2
  aiid: 13
  params:
    - '{"room_attrs":[{"id":8,"fan_level":1,"water_level":1,"clean_mode":3,"clean_times":1,"mop_mode":0,"on":true}]}'
```

Fire it before aiid 16 for a custom clean. The `room_attrs` format was first confirmed on the S20+ (`xiaomi.vacuum.b108gl`, same firmware family — there config=aiid 10, start=aiid 13) in [this thread](https://community.home-assistant.io/t/support-for-xiaomi-vacuum-s20-to-xiaomi-miio-integration/770596?page=2); on the d102gl the pair is aiid 13 + aiid 16.

## Cloud ack ≠ executed

This is the single most important reliability fact. `call_action` returning `code: 0` means the **cloud** accepted the RPC — not that the robot did anything. Observed drop windows:

- right after a previous clean, while the station is mop-washing (`StationWorking`): the start command can leave the robot **Paused with no room config** — an immediate identical re-fire went through cleanly;
- at the dock, during the normal `battery→0 / mop→False` display-state cycling, most commands are swallowed but an eventual retry lands (observed ~10 retries over ~3 min worst case).

**The reliable pattern:** after each aiid-16 call, poll the `vacuum.current_cleaning_config` attribute; success = it contains your `rooms`. If it doesn't within ~25 s, re-fire. That is exactly what this repo's `clean_rooms` service and blueprint implement.

Tip: call the action through the REST API with `?return_response` to at least get the cloud ack synchronously:

```
POST /api/services/xiaomi_miot/call_action?return_response
{"entity_id":"vacuum...","siid":2,"aiid":16,"params":["8"]}
→ {"service_response": {"code": 0, ...}}
```

## Deep sleep

The robot deep-sleeps within ~1-2 min of docking (`vacuum.sleep_status: true`; state can go `unknown`). Historically (early 2026 firmware) commands sent while sleeping were silently dropped and the workaround was waking it with `button.press start_sweep_mop`, then firing the room command during the resulting `StationWorking` phase (commands are **queued** during station work — the Xiaomi app itself relies on this). On firmware `0.0.23` this is no longer necessary: both aiid 13 and aiid 16 were accepted from deep sleep and the robot woke itself ~90 s later and executed. Keep the wake fallback in mind for older firmware.

## Room IDs

- Live in the `vacuum.room_information` attribute: `{"rooms":[{"id":6,"name":""},...],"map_uid":N}` (names are always empty — they only exist in the app's cloud map).
- **Regenerate on every map remake.** Automations with hardcoded IDs silently clean wrong rooms after a remap.
- **Do not match the app's room numbers.** Verified directly: a room the app labeled "room 4" was miot ID 6. The only reliable mapping: clean one ID, watch where the robot goes.

## Things that do NOT work

- `vacuum.send_command app_segment_clean` — routes to the local connection, which is `None` in cloud mode; fails silently.
- Writing `current_cleaning_config` (`piid 40`) via `set_miot_property` — read-only.
- Templating `params` as a comma string into `call_action` — HA coerces `"17,20"` into `[17, 20]` (list of ints) and the robot can end up in a stuck state needing a restart ([hass-xiaomi-miot#2735](https://github.com/al-one/hass-xiaomi-miot/issues/2735)). Hardcode, or build the string in Python/JSON as this integration does.
- The official `ha_xiaomi_home` integration for room cleaning — room IDs aren't exposed ([discussion #109](https://github.com/XiaoMi/ha_xiaomi_home/discussions/109)).

## Cloud session lifecycle

- `battery→0, mop→False` cycling at the dock every few minutes is **normal** display-state drop; commands still work while the integration is `loaded`.
- True session expiry: entity stuck at `unknown` — only a full HA restart recovers (`xiaomi_miot`'s config entry has `supports_unload: false`, so reloading the integration is a no-op).

## User-define presets (aiid 42) — SOLVED, live-verified

App-saved custom cleanups replay perfectly and are the practical route to zone cleaning.

1. Save a custom cleanup in the Xiaomi Home app (zone + settings).
2. Read it back: `xiaomi_miot.get_properties` with `siid:2 piid:42` →
   ```json
   {"user_labels":[{"id":1593689101,"name":"Dining table","v":1,"mop":[3],"room_ids":[],"user_cfg":[]}]}
   ```
3. Start it: `call_action siid:2 aiid:42 params:[1]` — **the param is the small `v` value (uint16), NOT the long `id`**. Sending the long id (or its string form) returns cloud `code:0` but the robot does nothing — this explains the X20 Max report of aiid 42 "not working" (they sent a long id).
4. Acceptance signature: `current_cleaning_config` becomes
   ```json
   {"user_define":{"id":1593689101,"v":1,"name":"...","user_data":[["mode","mop","fan","water","clean_count","more","ai","clean_path","mode_data"],[2,3,2,2,1,0,0,2,[[-960,-3908,-960,-6227,431,-6227,431,-3908]]]]}}
   ```
   Note `mode_data` holds the zone rectangle as 4 corner points in map mm — the robot's own storage format for zones.

<a name="zone-cleaning"></a>
## Zone cleaning via raw coordinates (aiid 12/37) — UNSOLVED

A captured app-triggered zone clean reports `current_cleaning_config: {"zones":[[x1,y1,...,x4,y4]],"clean_mode":2}` (4 corners, map mm). Replaying via `aiid:37 start-zone-sweep` was attempted with every plausible param encoding — exact captured nested array, 2-corner form, repeat-count suffix, flat array, bare CSV, Y-negated, and `aiid:12 set-zone` first; writing the `zone-ids` property directly returns `-704030023` (not writable). All action calls get cloud `code:0`; the flat/CSV forms make the robot navigate and then announce "could not reach the target location" — so the input parses, but the expected coordinate encoding/frame remains unknown. If you crack it, please open a PR. Until then: use presets (above).

## Still unexplored

- `aiid 38/39/40` add/modify/delete user presets programmatically (creating presets from HA would enable fully dynamic zone cleaning; the `user_cfg`/`mode_data` shape above is probably the payload).
- Whether commands are queued during `GoWash` (status 7) like they are during `StationWorking` (status 14).
