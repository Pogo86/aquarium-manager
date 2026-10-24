"""Editable aquarium water readings."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquariumManagerConfigEntry
from .const import CONF_TANK_NAME, DOMAIN, MANUFACTURER, MODEL, SIGNAL_STOCKING_UPDATED


@dataclass(frozen=True, kw_only=True)
class AquariumNumberDescription(NumberEntityDescription):
    """Description for an editable aquarium reading."""

    default_value: float | None = None
    icon: str | None = None


TANK_READINGS: tuple[AquariumNumberDescription, ...] = (
    AquariumNumberDescription(
        key="ammonia",
        name="Ammonia",
        icon="mdi:chemical-weapon",
        native_min_value=0,
        native_max_value=10,
        native_step=0.01,
        native_unit_of_measurement="mg/L",
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="nitrite",
        name="Nitrite",
        icon="mdi:flask-outline",
        native_min_value=0,
        native_max_value=10,
        native_step=0.01,
        native_unit_of_measurement="mg/L",
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="nitrate",
        name="Nitrate",
        icon="mdi:flask",
        native_min_value=0,
        native_max_value=250,
        native_step=0.5,
        native_unit_of_measurement="mg/L",
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="ph",
        name="pH",
        icon="mdi:ph",
        native_min_value=4,
        native_max_value=10,
        native_step=0.1,
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="gh",
        name="GH",
        icon="mdi:water-opacity",
        native_min_value=0,
        native_max_value=50,
        native_step=1,
        native_unit_of_measurement="dGH",
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="kh",
        name="KH",
        icon="mdi:water-percent",
        native_min_value=0,
        native_max_value=50,
        native_step=1,
        native_unit_of_measurement="dKH",
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="tds",
        name="TDS",
        icon="mdi:water-sync",
        native_min_value=0,
        native_max_value=2000,
        native_step=1,
        native_unit_of_measurement="ppm",
        mode=NumberMode.BOX,
    ),
)

SOURCE_READINGS: tuple[AquariumNumberDescription, ...] = (
    AquariumNumberDescription(
        key="source_nitrate",
        name="Source water nitrate",
        icon="mdi:home-water",
        default_value=2.5,
        native_min_value=0,
        native_max_value=250,
        native_step=0.5,
        native_unit_of_measurement="mg/L",
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="source_ph",
        name="Source water pH",
        icon="mdi:home-water",
        default_value=7.5,
        native_min_value=4,
        native_max_value=10,
        native_step=0.1,
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="source_gh",
        name="Source water GH",
        icon="mdi:home-water",
        default_value=4,
        native_min_value=0,
        native_max_value=50,
        native_step=1,
        native_unit_of_measurement="dGH",
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="source_kh",
        name="Source water KH",
        icon="mdi:home-water",
        default_value=2,
        native_min_value=0,
        native_max_value=50,
        native_step=1,
        native_unit_of_measurement="dKH",
        mode=NumberMode.BOX,
    ),
    AquariumNumberDescription(
        key="source_tds",
        name="Source water TDS",
        icon="mdi:home-water",
        native_min_value=0,
        native_max_value=2000,
        native_step=1,
        native_unit_of_measurement="ppm",
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquariumManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up editable aquarium readings."""
    async_add_entities(
        AquariumReadingNumber(entry, description)
        for description in (*TANK_READINGS, *SOURCE_READINGS)
    )


class AquariumReadingNumber(RestoreNumber):
    """An editable, restart-persistent aquarium reading."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: AquariumManagerConfigEntry,
        description: AquariumNumberDescription,
    ) -> None:
        """Initialize the reading."""
        self.entity_description = description
        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_native_value = description.default_value
        self._attr_icon = description.icon

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_TANK_NAME],
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last entered reading."""
        await super().async_added_to_hass()

        last_data = await self.async_get_last_number_data()
        if last_data is not None and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value

        self._entry.runtime_data.readings[
            self.entity_description.key
        ] = self._attr_native_value
        self._entry.runtime_data.reading_entities[
            self.entity_description.key
        ] = self

    async def async_will_remove_from_hass(self) -> None:
        """Remove this entity from the batch-reading registry."""
        self._entry.runtime_data.reading_entities.pop(
            self.entity_description.key,
            None,
        )
        await super().async_will_remove_from_hass()

    async def async_set_native_value(self, value: float) -> None:
        """Store a newly entered reading."""
        self._attr_native_value = value
        self._entry.runtime_data.readings[
            self.entity_description.key
        ] = value
        self.async_write_ha_state()
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_STOCKING_UPDATED}_{self._entry.entry_id}",
        )
