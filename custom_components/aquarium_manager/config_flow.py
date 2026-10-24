"""Config flow for Aquarium Manager."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from .const import (
    CONF_BACKUP_HEATER_SWITCH,
    CONF_BACKUP_HEATER_AUTOMATION_ENABLED,
    CONF_BACKUP_HEATER_ON_TEMP_C,
    CONF_BACKUP_HEATER_OFF_TEMP_C,
    CONF_BACKUP_HEATER_LOW_DELAY_MIN,
    CONF_BACKUP_HEATER_MAX_RUNTIME_MIN,
    CONF_WATER_TEST_NOTIFY_ENTITY,
    DEFAULT_BACKUP_HEATER_AUTOMATION_ENABLED,
    DEFAULT_BACKUP_HEATER_ON_TEMP_C,
    DEFAULT_BACKUP_HEATER_OFF_TEMP_C,
    DEFAULT_BACKUP_HEATER_LOW_DELAY_MIN,
    DEFAULT_BACKUP_HEATER_MAX_RUNTIME_MIN,
    CONF_GITHUB_TOKEN,
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
    CONF_LENGTH_CM,
    CONF_SPECIES_LIBRARY_URL,
    CONF_TANK_NAME,
    CONF_TANK_PROFILE,
    CONF_TEMPERATURE_ENTITY,
    CONF_WATER_DEPTH_CM,
    CONF_WATER_TYPE,
    CONF_WIDTH_CM,
    DOMAIN,
    WATER_TYPE_FRESHWATER,
    WATER_TYPE_MARINE,
    TANK_PROFILE_COMMUNITY,
    TANK_PROFILE_SHRIMP,
)
from .species import is_http_url


def _normalize_clock_time(value: Any) -> str | None:
    """Return a JSON-safe HH:MM:SS value, or None when invalid."""
    if value is None:
        return None

    # Be tolerant of a previously supplied datetime.time-like value while
    # migrating away from TimeSelector.
    if hasattr(value, "hour") and hasattr(value, "minute"):
        try:
            return f"{int(value.hour):02d}:{int(value.minute):02d}:{int(getattr(value, 'second', 0)):02d}"
        except (TypeError, ValueError):
            return None

    raw = str(value).strip()
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return f"{hour:02d}:{minute:02d}:{second:02d}"


class AquariumManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Aquarium Manager config flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return AquariumManagerOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create an aquarium."""
        if user_input is not None:
            tank_name = user_input[CONF_TANK_NAME].strip()

            await self.async_set_unique_id(slugify(tank_name))
            self._abort_if_unique_id_configured()

            user_input[CONF_TANK_NAME] = tank_name

            return self.async_create_entry(
                title=tank_name,
                data=user_input,
            )

        dimension_selector = NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=500,
                step=0.1,
                unit_of_measurement="cm",
                mode=NumberSelectorMode.BOX,
            )
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TANK_NAME,
                    default="Main Aquarium",
                ): TextSelector(),
                vol.Required(
                    CONF_LENGTH_CM,
                    default=80,
                ): dimension_selector,
                vol.Required(
                    CONF_WIDTH_CM,
                    default=35,
                ): dimension_selector,
                vol.Required(
                    CONF_WATER_DEPTH_CM,
                    default=42,
                ): dimension_selector,
                vol.Required(
                    CONF_WATER_TYPE,
                    default=WATER_TYPE_FRESHWATER,
                ): vol.In(
                    {
                        WATER_TYPE_FRESHWATER: "Freshwater",
                        WATER_TYPE_MARINE: "Marine",
                    }
                ),
                vol.Required(
                    CONF_TANK_PROFILE,
                    default=TANK_PROFILE_COMMUNITY,
                ): vol.In(
                    {
                        TANK_PROFILE_COMMUNITY: "Community aquarium",
                        TANK_PROFILE_SHRIMP: "Shrimp aquarium",
                    }
                ),
                vol.Optional(CONF_TEMPERATURE_ENTITY): EntitySelector(),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )


class AquariumManagerOptionsFlow(config_entries.OptionsFlowWithReload):
    """Configure optional Aquarium Manager behavior."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage Aquarium Manager options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            library_url = str(user_input.get(CONF_SPECIES_LIBRARY_URL, "") or "").strip()
            backup_switch = str(user_input.get(CONF_BACKUP_HEATER_SWITCH, "") or "").strip()
            water_notify = str(user_input.get(CONF_WATER_TEST_NOTIFY_ENTITY, "") or "").strip()
            github_token = str(user_input.get(CONF_GITHUB_TOKEN, "") or "").strip()
            user_input[CONF_GITHUB_TOKEN] = github_token
            light_1 = str(user_input.get(CONF_LIGHT_1_SWITCH, "") or "").strip()
            light_2 = str(user_input.get(CONF_LIGHT_2_SWITCH, "") or "").strip()
            co2_switch = str(user_input.get(CONF_CO2_SWITCH, "") or "").strip()
            lighting_enabled = bool(user_input.get(CONF_LIGHTING_AUTOMATION_ENABLED, False))
            light_duration = float(user_input.get(CONF_LIGHT_DURATION_HOURS, DEFAULT_LIGHT_DURATION_HOURS))
            half_duration = int(user_input.get(CONF_HALF_LIGHT_DURATION_MIN, DEFAULT_HALF_LIGHT_DURATION_MIN))
            co2_off_before = int(user_input.get(CONF_CO2_OFF_BEFORE_END_MIN, DEFAULT_CO2_OFF_BEFORE_END_MIN))
            lights_on_time = _normalize_clock_time(
                user_input.get(CONF_LIGHTS_ON_TIME, DEFAULT_LIGHTS_ON_TIME)
            )
            enabled = bool(user_input.get(CONF_BACKUP_HEATER_AUTOMATION_ENABLED, False))
            on_temp = float(user_input.get(CONF_BACKUP_HEATER_ON_TEMP_C, DEFAULT_BACKUP_HEATER_ON_TEMP_C))
            off_temp = float(user_input.get(CONF_BACKUP_HEATER_OFF_TEMP_C, DEFAULT_BACKUP_HEATER_OFF_TEMP_C))
            if library_url and not is_http_url(library_url):
                errors[CONF_SPECIES_LIBRARY_URL] = "invalid_url"
            elif backup_switch and not backup_switch.startswith("switch."):
                errors[CONF_BACKUP_HEATER_SWITCH] = "invalid_switch"
            elif water_notify and not water_notify.startswith("notify."):
                errors[CONF_WATER_TEST_NOTIFY_ENTITY] = "invalid_notify_entity"
            elif enabled and not backup_switch:
                errors[CONF_BACKUP_HEATER_SWITCH] = "heater_required"
            elif enabled and not self.config_entry.data.get(CONF_TEMPERATURE_ENTITY):
                errors[CONF_BACKUP_HEATER_AUTOMATION_ENABLED] = "temperature_required"
            elif off_temp <= on_temp:
                errors[CONF_BACKUP_HEATER_OFF_TEMP_C] = "off_must_exceed_on"
            elif lights_on_time is None:
                errors[CONF_LIGHTS_ON_TIME] = "invalid_time"
            elif any(entity and not entity.startswith("switch.") for entity in (light_1, light_2, co2_switch)):
                errors[CONF_LIGHT_1_SWITCH] = "invalid_switch"
            elif lighting_enabled and not (light_1 and light_2 and co2_switch):
                errors[CONF_LIGHTING_AUTOMATION_ENABLED] = "lighting_switches_required"
            elif light_1 and light_2 and light_1 == light_2:
                errors[CONF_LIGHT_2_SWITCH] = "lights_must_differ"
            elif half_duration > int(light_duration * 60):
                errors[CONF_HALF_LIGHT_DURATION_MIN] = "half_exceeds_duration"
            elif co2_off_before > int(light_duration * 60):
                errors[CONF_CO2_OFF_BEFORE_END_MIN] = "co2_off_exceeds_duration"
            else:
                user_input[CONF_LIGHTS_ON_TIME] = lights_on_time
                return self.async_create_entry(data=dict(user_input))

        current_url = str(self.config_entry.options.get(CONF_SPECIES_LIBRARY_URL, "") or "")
        current_github_token = str(self.config_entry.options.get(CONF_GITHUB_TOKEN, "") or "")
        current_backup_switch = str(self.config_entry.options.get(CONF_BACKUP_HEATER_SWITCH, "") or "")
        current_water_notify = str(self.config_entry.options.get(CONF_WATER_TEST_NOTIFY_ENTITY, "") or "")
        current_light_1 = str(self.config_entry.options.get(CONF_LIGHT_1_SWITCH, "") or "")
        current_light_2 = str(self.config_entry.options.get(CONF_LIGHT_2_SWITCH, "") or "")
        current_co2 = str(self.config_entry.options.get(CONF_CO2_SWITCH, "") or "")
        current_lights_on = _normalize_clock_time(
            self.config_entry.options.get(CONF_LIGHTS_ON_TIME, DEFAULT_LIGHTS_ON_TIME)
        ) or DEFAULT_LIGHTS_ON_TIME
        current_lighting_enabled = bool(self.config_entry.options.get(
            CONF_LIGHTING_AUTOMATION_ENABLED,
            DEFAULT_LIGHTING_AUTOMATION_ENABLED if (current_light_1 and current_light_2 and current_co2) else False,
        ))
        current_enabled = bool(self.config_entry.options.get(
            CONF_BACKUP_HEATER_AUTOMATION_ENABLED,
            DEFAULT_BACKUP_HEATER_AUTOMATION_ENABLED if current_backup_switch else False,
        ))

        temperature_selector = NumberSelector(NumberSelectorConfig(
            min=10, max=35, step=0.1, unit_of_measurement="°C", mode=NumberSelectorMode.BOX
        ))
        minutes_selector = NumberSelector(NumberSelectorConfig(
            min=1, max=720, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX
        ))
        duration_selector = NumberSelector(NumberSelectorConfig(
            min=4, max=10, step=0.5, unit_of_measurement="h", mode=NumberSelectorMode.BOX
        ))
        lighting_minutes_selector = NumberSelector(NumberSelectorConfig(
            min=0, max=360, step=5, unit_of_measurement="min", mode=NumberSelectorMode.BOX
        ))
        toggle_seconds_selector = NumberSelector(NumberSelectorConfig(
            min=0.2, max=5, step=0.1, unit_of_measurement="s", mode=NumberSelectorMode.BOX
        ))

        schema = vol.Schema({
            vol.Optional(CONF_SPECIES_LIBRARY_URL, default=current_url): TextSelector(),
            vol.Optional(CONF_GITHUB_TOKEN, default=current_github_token): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
            ),
            vol.Optional(CONF_BACKUP_HEATER_SWITCH, default=current_backup_switch): EntitySelector(),
            vol.Optional(CONF_WATER_TEST_NOTIFY_ENTITY, default=current_water_notify): EntitySelector(
                EntitySelectorConfig(domain="notify")
            ),
            vol.Optional(
                CONF_BACKUP_HEATER_AUTOMATION_ENABLED,
                default=current_enabled,
            ): bool,
            vol.Optional(
                CONF_BACKUP_HEATER_ON_TEMP_C,
                default=float(self.config_entry.options.get(
                    CONF_BACKUP_HEATER_ON_TEMP_C, DEFAULT_BACKUP_HEATER_ON_TEMP_C
                )),
            ): temperature_selector,
            vol.Optional(
                CONF_BACKUP_HEATER_OFF_TEMP_C,
                default=float(self.config_entry.options.get(
                    CONF_BACKUP_HEATER_OFF_TEMP_C, DEFAULT_BACKUP_HEATER_OFF_TEMP_C
                )),
            ): temperature_selector,
            vol.Optional(
                CONF_BACKUP_HEATER_LOW_DELAY_MIN,
                default=int(self.config_entry.options.get(
                    CONF_BACKUP_HEATER_LOW_DELAY_MIN, DEFAULT_BACKUP_HEATER_LOW_DELAY_MIN
                )),
            ): minutes_selector,
            vol.Optional(
                CONF_BACKUP_HEATER_MAX_RUNTIME_MIN,
                default=int(self.config_entry.options.get(
                    CONF_BACKUP_HEATER_MAX_RUNTIME_MIN, DEFAULT_BACKUP_HEATER_MAX_RUNTIME_MIN
                )),
            ): minutes_selector,
            vol.Optional(CONF_LIGHT_1_SWITCH, default=current_light_1): EntitySelector(),
            vol.Optional(CONF_LIGHT_2_SWITCH, default=current_light_2): EntitySelector(),
            vol.Optional(CONF_CO2_SWITCH, default=current_co2): EntitySelector(),
            vol.Optional(
                CONF_LIGHTING_AUTOMATION_ENABLED,
                default=current_lighting_enabled,
            ): bool,
            vol.Optional(
                CONF_LIGHTS_ON_TIME,
                default=current_lights_on,
            ): TextSelector(),
            vol.Optional(
                CONF_LIGHT_DURATION_HOURS,
                default=float(self.config_entry.options.get(CONF_LIGHT_DURATION_HOURS, DEFAULT_LIGHT_DURATION_HOURS)),
            ): duration_selector,
            vol.Optional(
                CONF_HALF_LIGHT_DURATION_MIN,
                default=int(self.config_entry.options.get(CONF_HALF_LIGHT_DURATION_MIN, DEFAULT_HALF_LIGHT_DURATION_MIN)),
            ): lighting_minutes_selector,
            vol.Optional(
                CONF_BLUE_LIGHT_DURATION_MIN,
                default=int(self.config_entry.options.get(CONF_BLUE_LIGHT_DURATION_MIN, DEFAULT_BLUE_LIGHT_DURATION_MIN)),
            ): lighting_minutes_selector,
            vol.Optional(
                CONF_CO2_LEAD_MIN,
                default=int(self.config_entry.options.get(CONF_CO2_LEAD_MIN, DEFAULT_CO2_LEAD_MIN)),
            ): lighting_minutes_selector,
            vol.Optional(
                CONF_CO2_OFF_BEFORE_END_MIN,
                default=int(self.config_entry.options.get(CONF_CO2_OFF_BEFORE_END_MIN, DEFAULT_CO2_OFF_BEFORE_END_MIN)),
            ): lighting_minutes_selector,
            vol.Optional(
                CONF_LIGHT_MODE_TOGGLE_SECONDS,
                default=float(self.config_entry.options.get(CONF_LIGHT_MODE_TOGGLE_SECONDS, DEFAULT_LIGHT_MODE_TOGGLE_SECONDS)),
            ): toggle_seconds_selector,
        })

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
