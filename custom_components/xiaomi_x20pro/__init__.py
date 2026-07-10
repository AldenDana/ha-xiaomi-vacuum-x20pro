"""Room cleaning services for the Xiaomi Robot Vacuum X20 Pro (xiaomi.vacuum.d102gl).

Thin wrapper around the `xiaomi_miot` (Xiaomi Miot Auto) integration's
`call_action` service. It exposes reliable room cleaning — including
per-room custom settings — for a model that no dedicated Home Assistant
integration supports.

Key device facts this integration encodes (live-verified 2026-07):
- siid:2 aiid:16 `start-vacuum-room-sweep` starts a room clean; params is a
  single comma-separated string of map room IDs, e.g. ["8"] or ["8,13"].
- siid:2 aiid:13 `set-room-clean-configs` stores per-room settings
  (fan/water/mode/passes) as a `room_attrs` JSON string. It never moves the
  robot and is accepted even in deep sleep.
- A cloud ack (code 0) does NOT mean the robot executed the command: right
  after a previous clean (station mop-wash) the command can be dropped or
  leave the robot Paused. The fix is verifying that the entity attribute
  `vacuum.current_cleaning_config` picks up the requested rooms, and
  re-firing if it did not.
"""

from __future__ import annotations

import asyncio
import json
import logging

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import ConfigType

DOMAIN = "xiaomi_x20pro"
_LOGGER = logging.getLogger(__name__)

MIOT_DOMAIN = "xiaomi_miot"
MIOT_SERVICE = "call_action"

SIID_VACUUM = 2
AIID_SET_ROOM_CONFIG = 13
AIID_START_ROOM_SWEEP = 16
AIID_START_USER_DEFINE_SWEEP = 42

# Station actions (no input params). Live-verified on d102gl 2026-07-10.
STATION_SERVICES = {
    "start_mop_wash": 19,
    "stop_mop_wash": 31,
    "start_dry": 20,
    "stop_dry": 32,
    "start_dust_collection": 18,
}

# xiaomi_miot exposes miot properties as dotted attribute names.
ATTR_CLEANING_CONFIG = "vacuum.current_cleaning_config"

SERVICE_CLEAN_ROOMS = "clean_rooms"
SERVICE_SET_ROOM_CONFIG = "set_room_config"
SERVICE_START_PRESET = "start_preset"

CONFIG_FIELDS = ("fan_level", "water_level", "clean_mode", "clean_times")

CLEAN_ROOMS_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("rooms"): vol.All(cv.ensure_list, [vol.Coerce(int)], vol.Length(min=1)),
        vol.Optional("fan_level"): vol.All(vol.Coerce(int), vol.In([1, 2, 3, 4])),
        vol.Optional("water_level"): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 3])),
        vol.Optional("clean_mode"): vol.All(vol.Coerce(int), vol.In([1, 2, 3, 4])),
        vol.Optional("clean_times"): vol.All(vol.Coerce(int), vol.In([1, 2, 3])),
        vol.Optional("retries", default=5): vol.All(vol.Coerce(int), vol.Range(min=0, max=30)),
        vol.Optional("retry_delay", default=25): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=120)
        ),
    }
)

ENTITY_ONLY_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_id})

START_PRESET_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("preset"): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
        vol.Optional("retries", default=5): vol.All(vol.Coerce(int), vol.Range(min=0, max=30)),
        vol.Optional("retry_delay", default=25): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=120)
        ),
    }
)

SET_ROOM_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("rooms"): vol.All(cv.ensure_list, [vol.Coerce(int)], vol.Length(min=1)),
        vol.Optional("fan_level", default=3): vol.All(vol.Coerce(int), vol.In([1, 2, 3, 4])),
        vol.Optional("water_level", default=2): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 3])),
        vol.Optional("clean_mode", default=3): vol.All(vol.Coerce(int), vol.In([1, 2, 3, 4])),
        vol.Optional("clean_times", default=1): vol.All(vol.Coerce(int), vol.In([1, 2, 3])),
    }
)


def _room_attrs_payload(rooms: list[int], data: dict) -> str:
    """Build the aiid:13 room_attrs JSON string."""
    attrs = [
        {
            "id": room,
            "fan_level": data.get("fan_level", 3),
            "water_level": data.get("water_level", 2),
            "clean_mode": data.get("clean_mode", 3),
            "clean_times": data.get("clean_times", 1),
            "mop_mode": 0,
            "on": True,
        }
        for room in rooms
    ]
    return json.dumps({"room_attrs": attrs}, separators=(",", ":"))


def _accepted_rooms(hass: HomeAssistant, entity_id: str) -> set[int]:
    """Return the rooms currently present in vacuum.current_cleaning_config."""
    state = hass.states.get(entity_id)
    if state is None:
        return set()
    raw = state.attributes.get(ATTR_CLEANING_CONFIG)
    if not raw:
        return set()
    try:
        return {int(r) for r in json.loads(raw).get("rooms", [])}
    except (ValueError, TypeError):
        return set()


async def _call_miot_action(
    hass: HomeAssistant, entity_id: str, aiid: int, params: list
) -> None:
    await hass.services.async_call(
        MIOT_DOMAIN,
        MIOT_SERVICE,
        {"entity_id": entity_id, "siid": SIID_VACUUM, "aiid": aiid, "params": params},
        blocking=True,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services."""

    if not hass.services.has_service(MIOT_DOMAIN, MIOT_SERVICE):
        _LOGGER.warning(
            "The xiaomi_miot (Xiaomi Miot Auto) integration is not loaded yet; "
            "services will fail until it is available"
        )

    async def set_room_config(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        rooms = call.data["rooms"]
        payload = _room_attrs_payload(rooms, dict(call.data))
        _LOGGER.debug("set_room_config %s -> %s", entity_id, payload)
        await _call_miot_action(hass, entity_id, AIID_SET_ROOM_CONFIG, [payload])

    async def clean_rooms(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        rooms: list[int] = call.data["rooms"]
        retries: int = call.data["retries"]
        retry_delay: int = call.data["retry_delay"]

        # Optional per-room config first (harmless, works even in deep sleep).
        if any(field in call.data for field in CONFIG_FIELDS):
            payload = _room_attrs_payload(rooms, dict(call.data))
            _LOGGER.debug("clean_rooms: sending room config %s", payload)
            await _call_miot_action(hass, entity_id, AIID_SET_ROOM_CONFIG, [payload])

        room_param = ",".join(str(room) for room in rooms)
        wanted = set(rooms)

        for attempt in range(retries + 1):
            _LOGGER.debug(
                "clean_rooms: start attempt %s/%s for rooms %s",
                attempt + 1,
                retries + 1,
                room_param,
            )
            await _call_miot_action(hass, entity_id, AIID_START_ROOM_SWEEP, [room_param])

            # The cloud ack does not mean the robot took the command. Poll the
            # reported cleaning config to confirm before declaring success.
            deadline = retry_delay
            while deadline > 0:
                await asyncio.sleep(5)
                deadline -= 5
                if wanted & _accepted_rooms(hass, entity_id):
                    _LOGGER.info(
                        "clean_rooms: rooms %s accepted on attempt %s",
                        room_param,
                        attempt + 1,
                    )
                    return

        raise HomeAssistantError(
            f"Vacuum did not accept room clean for rooms [{room_param}] after "
            f"{retries + 1} attempts. It may be mid station-work or the cloud "
            "session may have expired (entity state 'unknown' requires a Home "
            "Assistant restart to recover)."
        )

    async def start_preset(call: ServiceCall) -> None:
        """Start an app-saved custom cleanup (user-define sweep).

        The parameter is the small `v` value from the vacuum's
        `user_define_sweep_cfg` property (a uint16), NOT the long `id` label —
        the long id is accepted by the cloud (code 0) but the robot ignores it.
        Acceptance signature: `current_cleaning_config` gains a `user_define`
        key (or the vacuum starts cleaning).
        """
        entity_id = call.data["entity_id"]
        preset: int = call.data["preset"]
        retries: int = call.data["retries"]
        retry_delay: int = call.data["retry_delay"]

        def _accepted() -> bool:
            state = hass.states.get(entity_id)
            if state is None:
                return False
            if state.state == "cleaning":
                return True
            raw = state.attributes.get(ATTR_CLEANING_CONFIG) or ""
            return "user_define" in raw

        for attempt in range(retries + 1):
            _LOGGER.debug(
                "start_preset: attempt %s/%s for preset %s",
                attempt + 1,
                retries + 1,
                preset,
            )
            await _call_miot_action(
                hass, entity_id, AIID_START_USER_DEFINE_SWEEP, [preset]
            )
            deadline = retry_delay
            while deadline > 0:
                await asyncio.sleep(5)
                deadline -= 5
                if _accepted():
                    _LOGGER.info(
                        "start_preset: preset %s accepted on attempt %s",
                        preset,
                        attempt + 1,
                    )
                    return

        raise HomeAssistantError(
            f"Vacuum did not accept preset {preset} after {retries + 1} attempts. "
            "Check the preset exists (read the user_define_sweep_cfg property) and "
            "that you passed its small 'v' value, not the long label id."
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_ROOM_CONFIG, set_room_config, schema=SET_ROOM_CONFIG_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAN_ROOMS, clean_rooms, schema=CLEAN_ROOMS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_PRESET, start_preset, schema=START_PRESET_SCHEMA
    )

    def _make_station_handler(aiid: int):
        async def handler(call: ServiceCall) -> None:
            await _call_miot_action(hass, call.data["entity_id"], aiid, [])

        return handler

    for service_name, aiid in STATION_SERVICES.items():
        hass.services.async_register(
            DOMAIN, service_name, _make_station_handler(aiid), schema=ENTITY_ONLY_SCHEMA
        )
    return True
