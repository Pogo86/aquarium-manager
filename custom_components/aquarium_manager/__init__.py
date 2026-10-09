"""Aquarium Manager integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from pathlib import Path
from uuid import uuid4

import voluptuous as vol

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_AMMONIA,
    ATTR_ENTRY_ID,
    ATTR_GH,
    ATTR_KH,
    ATTR_NITRATE,
    ATTR_NITRITE,
    ATTR_PH,
    ATTR_NAME,
    ATTR_QUANTITY,
    ATTR_STOCK_ID,
    CONF_SPECIES_LIBRARY_URL,
    CONF_GITHUB_TOKEN,
    CONF_BACKUP_HEATER_SWITCH,
    CONF_BACKUP_HEATER_AUTOMATION_ENABLED,
    CONF_BACKUP_HEATER_ON_TEMP_C,
    CONF_BACKUP_HEATER_OFF_TEMP_C,
    CONF_BACKUP_HEATER_LOW_DELAY_MIN,
    CONF_BACKUP_HEATER_MAX_RUNTIME_MIN,
    CONF_LIGHT_1_SWITCH,
    CONF_LIGHT_2_SWITCH,
    CONF_CO2_SWITCH,
    CONF_LIGHTING_AUTOMATION_ENABLED,
    CONF_LIGHTS_ON_TIME,
    CONF_LIGHT_DURATION_HOURS,
    CONF_HALF_LIGHT_DURATION_MIN,
    CONF_BLUE_LIGHT_DURATION_MIN,
    CONF_CO2_LEAD_MIN,
    CONF_CO2_OFF_BEFORE_END_MIN,
    CONF_LIGHT_MODE_TOGGLE_SECONDS,
    DEFAULT_LIGHTING_AUTOMATION_ENABLED,
    DEFAULT_LIGHTS_ON_TIME,
    DEFAULT_LIGHT_DURATION_HOURS,
    DEFAULT_HALF_LIGHT_DURATION_MIN,
    DEFAULT_BLUE_LIGHT_DURATION_MIN,
    DEFAULT_CO2_LEAD_MIN,
    DEFAULT_CO2_OFF_BEFORE_END_MIN,
    DEFAULT_LIGHT_MODE_TOGGLE_SECONDS,
    CONF_TEMPERATURE_ENTITY,
    DEFAULT_BACKUP_HEATER_AUTOMATION_ENABLED,
    DEFAULT_BACKUP_HEATER_ON_TEMP_C,
    DEFAULT_BACKUP_HEATER_OFF_TEMP_C,
    DEFAULT_BACKUP_HEATER_LOW_DELAY_MIN,
    DEFAULT_BACKUP_HEATER_MAX_RUNTIME_MIN,
    DOMAIN,
    LIBRARY_REFRESH_HOURS,
    PLATFORMS,
    PROFILE_FIELDS,
    SERVICE_ADD_STOCK,
    SERVICE_REFRESH_LIBRARY,
    SERVICE_SUBMIT_WATER_READINGS,
    SERVICE_SEARCH_SPECIES,
    SERVICE_ADD_SPECIES_TO_LIBRARY,
    SERVICE_RESYNC_LIGHTING,
    SERVICE_UPDATE_LIGHTING_SCHEDULE,
    SERVICE_REMOVE_STOCK,
    SERVICE_UPDATE_STOCK,
    SIGNAL_LIBRARY_UPDATED,
    SIGNAL_STOCKING_UPDATED,
    STOCK_STORAGE_KEY_PREFIX,
    STOCK_STORAGE_VERSION,
)
from .species import SpeciesLibraryManager
from .species_importer import ImporterError, lookup_species_for_ha
from .github_library import GitHubLibraryError, GitHubLibraryWriter, github_target_from_raw_url
from .heater import BackupHeaterController
from .lighting import AquariumLightingController


@dataclass
class AquariumRuntimeData:
    """Runtime data for one aquarium."""

    hass: HomeAssistant
    entry_id: str
    library_url: str = ""
    readings: dict[str, float | None] = field(default_factory=dict)
    reading_entities: dict[str, Any] = field(default_factory=dict)
    last_reading_submitted: str | None = None
    stocking: list[dict[str, Any]] = field(default_factory=list)
    store: Store[dict[str, Any]] | None = None
    library: SpeciesLibraryManager | None = None
    heater_controller: BackupHeaterController | None = None
    lighting_controller: AquariumLightingController | None = None
    github_token: str = ""

    @property
    def github_write_ready(self) -> bool:
        return bool(self.github_token and github_target_from_raw_url(self.library_url))

    async def async_load(self) -> None:
        """Load fish library, then persisted aquarium data."""
        self.library = SpeciesLibraryManager(
            self.hass,
            self.entry_id,
            self.library_url,
        )
        await self.library.async_load()

        self.store = Store(
            self.hass,
            STOCK_STORAGE_VERSION,
            f"{STOCK_STORAGE_KEY_PREFIX}.{self.entry_id}",
        )
        saved = await self.store.async_load() or {}
        self.last_reading_submitted = saved.get("last_reading_submitted")
        stocking = saved.get("stocking", [])
        changed = False

        if isinstance(stocking, list):
            migrated: list[dict[str, Any]] = []
            for item in stocking:
                if not isinstance(item, dict):
                    continue
                enriched, item_changed = self.library.enrich_stock_item(item)
                migrated.append(enriched)
                changed = changed or item_changed
            self.stocking = migrated

        if changed:
            await self.async_save_stocking(notify=False)

    async def async_save_stocking(self, *, notify: bool = True) -> None:
        """Persist stocking data."""
        if self.store is None:
            return
        await self.store.async_save(
            {
                "stocking": self.stocking,
                "last_reading_submitted": self.last_reading_submitted,
            }
        )
        if notify:
            async_dispatcher_send(
                self.hass,
                f"{SIGNAL_STOCKING_UPDATED}_{self.entry_id}",
            )

    async def async_refresh_library(self, *, raise_on_error: bool = False) -> bool:
        """Refresh the species library and re-apply profiles to stocking."""
        if self.library is None:
            raise HomeAssistantError("Fish library is not loaded")

        try:
            success = await self.library.async_refresh(raise_on_error=raise_on_error)
            changed = False

            if success:
                refreshed: list[dict[str, Any]] = []
                for item in self.stocking:
                    enriched, item_changed = self.library.enrich_stock_item(item)
                    refreshed.append(enriched)
                    changed = changed or item_changed
                self.stocking = refreshed
                if changed:
                    await self.async_save_stocking(notify=False)
            return success
        finally:
            # Update status entities even when a manual refresh raises an error.
            async_dispatcher_send(
                self.hass,
                f"{SIGNAL_LIBRARY_UPDATED}_{self.entry_id}",
            )
            async_dispatcher_send(
                self.hass,
                f"{SIGNAL_STOCKING_UPDATED}_{self.entry_id}",
            )


AquariumManagerConfigEntry = ConfigEntry[AquariumRuntimeData]


def _runtime_or_raise(hass: HomeAssistant, entry_id: str) -> AquariumRuntimeData:
    """Return loaded runtime data for a config entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry_id)
    if not isinstance(runtime, AquariumRuntimeData):
        raise HomeAssistantError(
            "Aquarium is not loaded. Check the config entry ID and integration state."
        )
    return runtime


def _optional_number_schema() -> vol.Any:
    return vol.Any(None, vol.Coerce(float))


def _optional_int_schema() -> vol.Any:
    return vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=1, max=999)))


def _normalize_lighting_time(value: str) -> str:
    """Validate and normalize a dashboard-submitted clock time."""
    raw = str(value or "").strip()
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise HomeAssistantError("Lights-on time must be HH:MM or HH:MM:SS")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as err:
        raise HomeAssistantError("Lights-on time must contain numbers") from err
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise HomeAssistantError("Lights-on time is outside the valid clock range")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Aquarium Manager, frontend assets, and register actions."""
    hass.data.setdefault(DOMAIN, {})

    # Serve the bundled Lovelace card from a stable, version-independent URL.
    # HACS updates files inside the integration directory, so dashboards never
    # need a new resource URL for each Aquarium Manager release.
    frontend_path = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/aquarium_manager",
                str(frontend_path),
                False,  # Avoid stale JS after an integration update.
            )
        ]
    )

    add_schema = vol.Schema(
        {
            vol.Required(ATTR_ENTRY_ID): cv.string,
            vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=100)),
            vol.Required(ATTR_QUANTITY): vol.All(vol.Coerce(int), vol.Range(min=1, max=999)),
            vol.Optional("scientific_name", default=""): vol.All(cv.string, vol.Length(max=120)),
            vol.Optional("notes", default=""): vol.All(cv.string, vol.Length(max=500)),
        }
    )

    update_schema_dict: dict[Any, Any] = {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_STOCK_ID): cv.string,
        vol.Optional(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=100)),
        vol.Optional(ATTR_QUANTITY): vol.All(vol.Coerce(int), vol.Range(min=1, max=999)),
        vol.Optional("scientific_name"): vol.Any(None, vol.All(cv.string, vol.Length(max=120))),
        vol.Optional("notes"): vol.Any(None, vol.All(cv.string, vol.Length(max=500))),
        vol.Optional("adult_size_cm"): _optional_number_schema(),
        vol.Optional("temperature_min"): _optional_number_schema(),
        vol.Optional("temperature_max"): _optional_number_schema(),
        vol.Optional("ph_min"): _optional_number_schema(),
        vol.Optional("ph_max"): _optional_number_schema(),
        vol.Optional("gh_min"): _optional_number_schema(),
        vol.Optional("gh_max"): _optional_number_schema(),
        vol.Optional("kh_min"): _optional_number_schema(),
        vol.Optional("kh_max"): _optional_number_schema(),
        vol.Optional("min_group_size"): _optional_int_schema(),
        vol.Optional("min_tank_length_cm"): _optional_number_schema(),
        vol.Optional("min_tank_width_cm"): _optional_number_schema(),
        vol.Optional("min_tank_height_cm"): _optional_number_schema(),
        vol.Optional("social_type"): vol.Any(None, vol.All(cv.string, vol.Length(max=80))),
        vol.Optional("temperament"): vol.Any(None, vol.All(cv.string, vol.Length(max=160))),
        vol.Optional("swimming_zone"): vol.Any(None, vol.All(cv.string, vol.Length(max=80))),
    }
    update_schema = vol.Schema(update_schema_dict)

    remove_schema = vol.Schema(
        {
            vol.Required(ATTR_ENTRY_ID): cv.string,
            vol.Required(ATTR_STOCK_ID): cv.string,
        }
    )

    refresh_schema = vol.Schema(
        {
            vol.Required(ATTR_ENTRY_ID): cv.string,
        }
    )

    resync_lighting_schema = vol.Schema(
        {
            vol.Required(ATTR_ENTRY_ID): cv.string,
        }
    )

    update_lighting_schedule_schema = vol.Schema(
        {
            vol.Required(ATTR_ENTRY_ID): cv.string,
            vol.Required(CONF_LIGHTING_AUTOMATION_ENABLED): bool,
            vol.Required(CONF_LIGHTS_ON_TIME): vol.All(cv.string, vol.Length(min=4, max=8)),
            vol.Required(CONF_LIGHT_DURATION_HOURS): vol.All(vol.Coerce(float), vol.Range(min=4, max=10)),
            vol.Required(CONF_HALF_LIGHT_DURATION_MIN): vol.All(vol.Coerce(int), vol.Range(min=0, max=360)),
            vol.Required(CONF_BLUE_LIGHT_DURATION_MIN): vol.All(vol.Coerce(int), vol.Range(min=0, max=360)),
            vol.Required(CONF_CO2_LEAD_MIN): vol.All(vol.Coerce(int), vol.Range(min=0, max=360)),
            vol.Required(CONF_CO2_OFF_BEFORE_END_MIN): vol.All(vol.Coerce(int), vol.Range(min=0, max=360)),
        }
    )

    search_species_schema = vol.Schema({
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required("query"): vol.All(cv.string, vol.Length(min=2, max=160)),
    })

    add_species_library_schema = vol.Schema({
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required("species"): dict,
    })

    water_readings_schema = vol.Schema(
        {
            vol.Required(ATTR_ENTRY_ID): cv.string,
            vol.Required(ATTR_AMMONIA): vol.All(vol.Coerce(float), vol.Range(min=0, max=10)),
            vol.Required(ATTR_NITRITE): vol.All(vol.Coerce(float), vol.Range(min=0, max=10)),
            vol.Required(ATTR_NITRATE): vol.All(vol.Coerce(float), vol.Range(min=0, max=250)),
            vol.Required(ATTR_PH): vol.All(vol.Coerce(float), vol.Range(min=4, max=10)),
            vol.Required(ATTR_GH): vol.All(vol.Coerce(float), vol.Range(min=0, max=50)),
            vol.Required(ATTR_KH): vol.All(vol.Coerce(float), vol.Range(min=0, max=50)),
        }
    )

    async def async_add_stock(call: ServiceCall) -> None:
        """Add a livestock record and apply a known library profile."""
        runtime = _runtime_or_raise(hass, call.data[ATTR_ENTRY_ID])
        if runtime.library is None:
            raise HomeAssistantError("Fish library is not loaded")

        name = call.data[ATTR_NAME].strip()
        item: dict[str, Any] = {
            "id": uuid4().hex[:12],
            "name": name,
            "quantity": int(call.data[ATTR_QUANTITY]),
            "scientific_name": call.data.get("scientific_name", "").strip(),
            "notes": call.data.get("notes", "").strip(),
            "profile_overrides": [],
        }
        item, _ = runtime.library.enrich_stock_item(item)
        runtime.stocking.append(item)
        await runtime.async_save_stocking()

    async def async_update_stock(call: ServiceCall) -> None:
        """Update a livestock record and remember fields manually overridden by the user."""
        runtime = _runtime_or_raise(hass, call.data[ATTR_ENTRY_ID])
        if runtime.library is None:
            raise HomeAssistantError("Fish library is not loaded")

        stock_id = call.data[ATTR_STOCK_ID]

        for index, existing in enumerate(runtime.stocking):
            if existing.get("id") != stock_id:
                continue

            updated = dict(existing)
            old_name = str(updated.get("name", ""))
            overrides = {
                str(value)
                for value in updated.get("profile_overrides", [])
                if value in PROFILE_FIELDS
            }

            for key, value in call.data.items():
                if key in {ATTR_ENTRY_ID, ATTR_STOCK_ID}:
                    continue
                if isinstance(value, str):
                    value = value.strip()
                updated[key] = value
                if key in PROFILE_FIELDS:
                    overrides.add(key)

            new_name = str(updated.get("name", ""))
            if new_name and new_name.casefold() != old_name.casefold():
                profile = runtime.library.match(new_name)
                if profile is not None:
                    # Selecting a different known species starts from that library
                    # profile. Quantity and personal notes are retained.
                    updated = {
                        "id": updated.get("id", stock_id),
                        "name": new_name,
                        "quantity": updated.get("quantity", 1),
                        "notes": updated.get("notes", ""),
                        "profile_overrides": [],
                    }
                    updated, _ = runtime.library.enrich_stock_item(updated)
                else:
                    updated["profile_id"] = None
                    updated["profile_overrides"] = sorted(overrides)
                    updated["source_label"] = ""
                    updated["source_url"] = ""
                    updated["cautions"] = []
            else:
                updated["profile_overrides"] = sorted(overrides)

            runtime.stocking[index] = updated
            await runtime.async_save_stocking()
            return

        raise HomeAssistantError(f"Stocking record {stock_id} was not found.")

    async def async_remove_stock(call: ServiceCall) -> None:
        """Remove a livestock record."""
        runtime = _runtime_or_raise(hass, call.data[ATTR_ENTRY_ID])
        stock_id = call.data[ATTR_STOCK_ID]
        new_stocking = [
            item for item in runtime.stocking if item.get("id") != stock_id
        ]

        if len(new_stocking) == len(runtime.stocking):
            raise HomeAssistantError(f"Stocking record {stock_id} was not found.")

        runtime.stocking = new_stocking
        await runtime.async_save_stocking()

    async def async_refresh_library(call: ServiceCall) -> None:
        """Refresh a configured remote species library immediately."""
        runtime = _runtime_or_raise(hass, call.data[ATTR_ENTRY_ID])
        await runtime.async_refresh_library(raise_on_error=True)

    async def async_resync_lighting(call: ServiceCall) -> None:
        """Force the mode-cycling lights back to a known state and reapply schedule."""
        runtime = _runtime_or_raise(hass, call.data[ATTR_ENTRY_ID])
        if runtime.lighting_controller is None:
            raise HomeAssistantError("Lighting automation is not configured")
        await runtime.lighting_controller.async_resync()

    async def async_update_lighting_schedule(call: ServiceCall) -> dict[str, Any]:
        """Persist lighting timing from the Aquarium Manager dashboard."""
        entry_id = call.data[ATTR_ENTRY_ID]
        runtime = _runtime_or_raise(hass, entry_id)
        controller = runtime.lighting_controller
        if controller is None:
            raise HomeAssistantError(
                "Lighting is not configured. Select two light switches and a CO2 switch first."
            )

        enabled = bool(call.data[CONF_LIGHTING_AUTOMATION_ENABLED])
        lights_on_time = _normalize_lighting_time(call.data[CONF_LIGHTS_ON_TIME])
        duration = float(call.data[CONF_LIGHT_DURATION_HOURS])
        half = int(call.data[CONF_HALF_LIGHT_DURATION_MIN])
        blue = int(call.data[CONF_BLUE_LIGHT_DURATION_MIN])
        lead = int(call.data[CONF_CO2_LEAD_MIN])
        co2_off = int(call.data[CONF_CO2_OFF_BEFORE_END_MIN])
        duration_min = int(round(duration * 60))
        if half > duration_min:
            raise HomeAssistantError("Half-light duration cannot exceed the main photoperiod")
        if co2_off > duration_min:
            raise HomeAssistantError("CO2 shutoff offset cannot exceed the main photoperiod")

        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise HomeAssistantError("Aquarium config entry was not found")

        options = dict(entry.options)
        options.update({
            CONF_LIGHTING_AUTOMATION_ENABLED: enabled,
            CONF_LIGHTS_ON_TIME: lights_on_time,
            CONF_LIGHT_DURATION_HOURS: duration,
            CONF_HALF_LIGHT_DURATION_MIN: half,
            CONF_BLUE_LIGHT_DURATION_MIN: blue,
            CONF_CO2_LEAD_MIN: lead,
            CONF_CO2_OFF_BEFORE_END_MIN: co2_off,
        })
        hass.config_entries.async_update_entry(entry, options=options)

        # Apply immediately as well as persisting. This avoids requiring an
        # integration reload when the timing is changed from the Lovelace card.
        controller.enabled = enabled
        controller.lights_on_time = lights_on_time
        controller.light_duration_hours = duration
        controller.half_light_duration_min = half
        controller.blue_light_duration_min = blue
        controller.co2_lead_min = lead
        controller.co2_off_before_end_min = co2_off
        await controller.async_evaluate(force_sync=True)
        return {
            "saved": True,
            "status": controller.status,
            **controller.status_attributes,
        }

    async def async_submit_water_readings(call: ServiceCall) -> None:
        """Submit one complete aquarium water-test snapshot."""
        runtime = _runtime_or_raise(hass, call.data[ATTR_ENTRY_ID])
        keys = (ATTR_AMMONIA, ATTR_NITRITE, ATTR_NITRATE, ATTR_PH, ATTR_GH, ATTR_KH)
        missing_entities = [key for key in keys if key not in runtime.reading_entities]
        if missing_entities:
            raise HomeAssistantError(
                "Water-reading entities are not ready: " + ", ".join(missing_entities)
            )

        # Validation happens in the service schema before any value is changed,
        # so a submitted test is applied as one coherent set.
        for key in keys:
            entity = runtime.reading_entities[key]
            await entity.async_set_native_value(float(call.data[key]))

        runtime.last_reading_submitted = datetime.now(UTC).isoformat()
        await runtime.async_save_stocking(notify=False)
        async_dispatcher_send(
            hass,
            f"{SIGNAL_STOCKING_UPDATED}_{runtime.entry_id}",
        )

    async def async_search_species(call: ServiceCall) -> dict[str, Any]:
        """Search SeriouslyFish/FishBase and return a preview record."""
        runtime = _runtime_or_raise(hass, call.data[ATTR_ENTRY_ID])
        query = str(call.data["query"]).strip()
        cache_anchor = hass.config.path(".storage", f"aquarium_manager_species_lookup_{runtime.entry_id}.json")
        try:
            result = await hass.async_add_executor_job(lookup_species_for_ha, query, cache_anchor)
        except ImporterError as err:
            raise HomeAssistantError(f"Could not resolve species: {err}") from err
        record = result.get("record") or {}
        existing = False
        if runtime.library is not None:
            existing = bool(
                runtime.library.by_id(str(record.get("id") or ""))
                or runtime.library.match(str(record.get("scientific_name") or ""))
            )
        result["already_in_library"] = existing
        result["github_write_ready"] = runtime.github_write_ready
        return result

    async def async_add_species_to_library(call: ServiceCall) -> dict[str, Any]:
        """Commit one reviewed species record to the configured GitHub library."""
        runtime = _runtime_or_raise(hass, call.data[ATTR_ENTRY_ID])
        if runtime.library is None:
            raise HomeAssistantError("Fish library is not loaded")
        record = dict(call.data["species"])
        writer = GitHubLibraryWriter(hass, runtime.library_url, runtime.github_token)
        try:
            result = await writer.async_upsert(record)
            await runtime.library.async_apply_document(result["document"])
        except GitHubLibraryError as err:
            raise HomeAssistantError(f"Could not update GitHub fish library: {err}") from err

        # Re-enrich any stocked fish that reference the updated profile.
        refreshed = []
        changed = False
        for item in runtime.stocking:
            enriched, item_changed = runtime.library.enrich_stock_item(item)
            refreshed.append(enriched)
            changed = changed or item_changed
        runtime.stocking = refreshed
        if changed:
            await runtime.async_save_stocking(notify=False)
        async_dispatcher_send(hass, f"{SIGNAL_LIBRARY_UPDATED}_{runtime.entry_id}")
        async_dispatcher_send(hass, f"{SIGNAL_STOCKING_UPDATED}_{runtime.entry_id}")
        return {
            "action": result["action"],
            "commit_message": result["commit_message"],
            "commit_sha": result["commit_sha"],
            "species_count": len(runtime.library.profiles),
        }

    if not hass.services.has_service(DOMAIN, SERVICE_ADD_STOCK):
        hass.services.async_register(
            DOMAIN,
            SERVICE_ADD_STOCK,
            async_add_stock,
            schema=add_schema,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_STOCK):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_STOCK,
            async_update_stock,
            schema=update_schema,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REMOVE_STOCK):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REMOVE_STOCK,
            async_remove_stock,
            schema=remove_schema,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_LIBRARY):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_LIBRARY,
            async_refresh_library,
            schema=refresh_schema,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SUBMIT_WATER_READINGS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SUBMIT_WATER_READINGS,
            async_submit_water_readings,
            schema=water_readings_schema,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEARCH_SPECIES):
        hass.services.async_register(
            DOMAIN, SERVICE_SEARCH_SPECIES, async_search_species,
            schema=search_species_schema, supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_ADD_SPECIES_TO_LIBRARY):
        hass.services.async_register(
            DOMAIN, SERVICE_ADD_SPECIES_TO_LIBRARY, async_add_species_to_library,
            schema=add_species_library_schema, supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_RESYNC_LIGHTING):
        hass.services.async_register(
            DOMAIN, SERVICE_RESYNC_LIGHTING, async_resync_lighting,
            schema=resync_lighting_schema,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_LIGHTING_SCHEDULE):
        hass.services.async_register(
            DOMAIN, SERVICE_UPDATE_LIGHTING_SCHEDULE, async_update_lighting_schedule,
            schema=update_lighting_schedule_schema, supports_response=SupportsResponse.ONLY,
        )

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: AquariumManagerConfigEntry
) -> bool:
    """Set up Aquarium Manager from a config entry."""
    library_url = str(entry.options.get(CONF_SPECIES_LIBRARY_URL, "") or "").strip()
    runtime = AquariumRuntimeData(
        hass=hass,
        entry_id=entry.entry_id,
        library_url=library_url,
        github_token=str(entry.options.get(CONF_GITHUB_TOKEN, "") or "").strip(),
    )
    await runtime.async_load()

    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    backup_switch = str(entry.options.get(CONF_BACKUP_HEATER_SWITCH, "") or "").strip()
    temperature_entity = str(entry.data.get(CONF_TEMPERATURE_ENTITY, "") or "").strip()
    if backup_switch and temperature_entity:
        runtime.heater_controller = BackupHeaterController(
            hass,
            entry_id=entry.entry_id,
            temperature_entity=temperature_entity,
            heater_switch=backup_switch,
            enabled=bool(entry.options.get(
                CONF_BACKUP_HEATER_AUTOMATION_ENABLED,
                DEFAULT_BACKUP_HEATER_AUTOMATION_ENABLED,
            )),
            on_temp_c=float(entry.options.get(
                CONF_BACKUP_HEATER_ON_TEMP_C, DEFAULT_BACKUP_HEATER_ON_TEMP_C
            )),
            off_temp_c=float(entry.options.get(
                CONF_BACKUP_HEATER_OFF_TEMP_C, DEFAULT_BACKUP_HEATER_OFF_TEMP_C
            )),
            low_delay_min=int(entry.options.get(
                CONF_BACKUP_HEATER_LOW_DELAY_MIN, DEFAULT_BACKUP_HEATER_LOW_DELAY_MIN
            )),
            max_runtime_min=int(entry.options.get(
                CONF_BACKUP_HEATER_MAX_RUNTIME_MIN, DEFAULT_BACKUP_HEATER_MAX_RUNTIME_MIN
            )),
        )
        await runtime.heater_controller.async_start()

    light_1 = str(entry.options.get(CONF_LIGHT_1_SWITCH, "") or "").strip()
    light_2 = str(entry.options.get(CONF_LIGHT_2_SWITCH, "") or "").strip()
    co2_switch = str(entry.options.get(CONF_CO2_SWITCH, "") or "").strip()
    if light_1 or light_2 or co2_switch:
        runtime.lighting_controller = AquariumLightingController(
            hass,
            entry_id=entry.entry_id,
            light_switches=[light_1, light_2],
            co2_switch=co2_switch,
            enabled=bool(entry.options.get(
                CONF_LIGHTING_AUTOMATION_ENABLED,
                DEFAULT_LIGHTING_AUTOMATION_ENABLED,
            )),
            lights_on_time=str(entry.options.get(
                CONF_LIGHTS_ON_TIME, DEFAULT_LIGHTS_ON_TIME
            )),
            light_duration_hours=float(entry.options.get(
                CONF_LIGHT_DURATION_HOURS, DEFAULT_LIGHT_DURATION_HOURS
            )),
            half_light_duration_min=int(entry.options.get(
                CONF_HALF_LIGHT_DURATION_MIN, DEFAULT_HALF_LIGHT_DURATION_MIN
            )),
            blue_light_duration_min=int(entry.options.get(
                CONF_BLUE_LIGHT_DURATION_MIN, DEFAULT_BLUE_LIGHT_DURATION_MIN
            )),
            co2_lead_min=int(entry.options.get(
                CONF_CO2_LEAD_MIN, DEFAULT_CO2_LEAD_MIN
            )),
            co2_off_before_end_min=int(entry.options.get(
                CONF_CO2_OFF_BEFORE_END_MIN, DEFAULT_CO2_OFF_BEFORE_END_MIN
            )),
            mode_toggle_seconds=float(entry.options.get(
                CONF_LIGHT_MODE_TOGGLE_SECONDS, DEFAULT_LIGHT_MODE_TOGGLE_SECONDS
            )),
        )
        await runtime.lighting_controller.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _scheduled_library_refresh(_now: Any) -> None:
        if runtime.library_url:
            await runtime.async_refresh_library(raise_on_error=False)

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            _scheduled_library_refresh,
            timedelta(hours=LIBRARY_REFRESH_HOURS),
        )
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AquariumManagerConfigEntry
) -> bool:
    """Unload an Aquarium Manager config entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if isinstance(runtime, AquariumRuntimeData) and runtime.heater_controller is not None:
        await runtime.heater_controller.async_stop()
    if isinstance(runtime, AquariumRuntimeData) and runtime.lighting_controller is not None:
        await runtime.lighting_controller.async_stop()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
