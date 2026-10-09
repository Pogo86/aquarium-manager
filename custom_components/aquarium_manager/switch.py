"""Backup-heater proxy switch for Aquarium Manager."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import AquariumManagerConfigEntry
from .const import (
    CONF_BACKUP_HEATER_SWITCH,
    CONF_TANK_NAME,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    SIGNAL_LIGHTING_UPDATED,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquariumManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the optional backup-heater proxy."""
    entities: list[SwitchEntity] = []
    target = str(entry.options.get(CONF_BACKUP_HEATER_SWITCH, "") or "").strip()
    if target:
        entities.append(AquariumBackupHeaterSwitch(entry, target))
    if entry.runtime_data.lighting_controller is not None:
        entities.append(AquariumLightsOffTodaySwitch(entry))
    if entities:
        async_add_entities(entities)


class AquariumBackupHeaterSwitch(SwitchEntity):
    """Mirror and control an existing Zigbee/Wi-Fi heater plug switch."""

    _attr_has_entity_name = True
    _attr_name = "Backup heater"
    _attr_icon = "mdi:radiator"

    def __init__(self, entry: AquariumManagerConfigEntry, target: str) -> None:
        self._entry = entry
        self._target = target
        self._attr_unique_id = f"{entry.entry_id}_backup_heater"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_TANK_NAME],
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def available(self) -> bool:
        state = self.hass.states.get(self._target)
        return state is not None and state.state not in {"unknown", "unavailable"}

    @property
    def is_on(self) -> bool:
        state = self.hass.states.get(self._target)
        return state is not None and state.state == STATE_ON

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"source_entity": self._target}

    async def async_turn_on(self, **kwargs) -> None:
        await self.hass.services.async_call(
            "switch", "turn_on", {"entity_id": self._target}, blocking=True
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self.hass.services.async_call(
            "switch", "turn_off", {"entity_id": self._target}, blocking=True
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        async def _target_changed(_event: Event) -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._target],
                _target_changed,
            )
        )


class AquariumLightsOffTodaySwitch(SwitchEntity):
    """One-day override that keeps aquarium lights and CO2 off."""

    _attr_has_entity_name = True
    _attr_name = "Lights off today"
    _attr_icon = "mdi:lightbulb-off-outline"

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_lights_off_today"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_TANK_NAME],
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def available(self) -> bool:
        return self._entry.runtime_data.lighting_controller is not None

    @property
    def is_on(self) -> bool:
        controller = self._entry.runtime_data.lighting_controller
        return bool(controller and controller.is_day_off)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        controller = self._entry.runtime_data.lighting_controller
        return {
            "override_date": controller.day_off_date if controller else None,
            "behavior": "Turns both aquarium lights and CO2 off for the current local calendar day",
        }

    async def async_turn_on(self, **kwargs) -> None:
        controller = self._entry.runtime_data.lighting_controller
        if controller is not None:
            await controller.async_set_day_off(True)

    async def async_turn_off(self, **kwargs) -> None:
        controller = self._entry.runtime_data.lighting_controller
        if controller is not None:
            await controller.async_set_day_off(False)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_LIGHTING_UPDATED}_{self._entry.entry_id}",
                self.async_write_ha_state,
            )
        )
