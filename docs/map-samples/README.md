# Raw map samples — xiaomi.vacuum.d102gl (X20 Pro)

Samples captured 2026-07-10 (firmware 0.0.23, EU/de server) to help map-format
reverse engineering for the `xiaomi.vacuum.*` family
(PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor#558).

## KEY FINDING — how to download maps for this family

The legacy endpoints fail for `xiaomi.vacuum.*` models:

- `/home/getmapfileurl` → `code -8, "objectname invalid"`
- `/v2/home/get_interim_file_url` → `code -6, "invalid config for fds"`

The working endpoint is the undocumented **`/v2/home/get_interim_file_url_pro`**
with `{"obj_name": "<userId>/<did>/<slot>"}` — it returns a signed FDS URL in a
**per-model bucket** (`https://awsde0-fusion.fds.api.xiaomi.com/xiaomi-d102gl/...`),
which is why the generic bucket lookup of the older APIs errors out.

Object names come from the vacuum's properties: `vacuum_map.map_obj_name`
(`<userId>/<did>/0`), `vacuum_map.trajectory_obj_name` (`.../1`), and
`vacuum_map.map_management` (`map_array[].obj_name`, current map here `.../4`).

## File structure (as far as known)

Each download is JSON: `{"version": 2, "data": "<base64>"}`. The base64 payload
is high-entropy from byte 0 — not zlib/gzip/lz4-frame — i.e. most likely
**encrypted** (key derivation unknown; possibly device-token or account based).

| file | slot | meaning | b64-decoded size |
|---|---|---|---|
| d102gl_map_slot0.bin | 0 | map_obj_name (map slot 0) | 7968 B |
| d102gl_map_slot2.bin | 2 | unknown (small) | 1248 B |
| d102gl_map_slot4.bin | 4 | current saved map ("Map2", 8 rooms) | 3776 B |
| d102gl_map_slot8.bin | 8 | backup map | 3584 B |

Ground truth for this map (from `vacuum.room_information` and live testing):
8 rooms with ids 6, 8, 10, 13, 17, 19, 22, 23; a zone rectangle at
`[[-960,-3908,-960,-6227,431,-6227,431,-3908]]` (map mm) exists as a saved
preset. Restricted zones and furniture polygons are visible in the entity
attributes (`vacuum.restricted_sweep_areas`, `vacuum.sweep_furniture`) if
coordinate cross-referencing helps.
