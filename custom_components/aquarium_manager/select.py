"""Select entities for Aquarium Manager."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquariumManagerConfigEntry
from .const import (
    CONF_BACKUP_HEATER_AUTOMATION_ENABLED,
    CONF_TANK_NAME,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    SIGNAL_HEATER_UPDATED,
)


async def async_setup_entry(
    hass,
    entry: AquariumManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Aquarium Manager select entities."""
    if entry.runtime_data.heater_controller is not None:
        async_add_entities([AquariumBackupHeaterModeSelect(entry)])


class AquariumBackupHeaterModeSelect(SelectEntity):
    """Expose the backup heater controller as an Auto/Off mode selector."""

    _attr_has_entity_name = True
    _attr_name = "Backup heater mode"
    _attr_icon = "mdi:radiator"
    _attr_options = ["Auto", "Off"]

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_backup_heater_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_TANK_NAME],
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def available(self) -> bool:
        return self._entry.runtime_data.heater_controller is not None

    @property
    def current_option(self) -> str:
        controller = self._entry.runtime_data.heater_controller
        return "Auto" if controller and controller.enabled else "Off"

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unsupported backup heater mode: {option}")
        controller = self._entry.runtime_data.heater_controller
        if controller is None:
            return

        enabled = option == "Auto"
        options = dict(self._entry.options)
        options[CONF_BACKUP_HEATER_AUTOMATION_ENABLED] = enabled
        self.hass.config_entries.async_update_entry(self._entry, options=options)
        await controller.async_set_enabled(enabled)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_HEATER_UPDATED}_{self._entry.entry_id}",
                self.async_write_ha_state,
            )
        )
