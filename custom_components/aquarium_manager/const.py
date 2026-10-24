"""Constants for Aquarium Manager."""

DOMAIN = "aquarium_manager"

PLATFORMS = ["number", "sensor", "switch", "select"]

CONF_TANK_NAME = "tank_name"
CONF_LENGTH_CM = "length_cm"
CONF_WIDTH_CM = "width_cm"
CONF_WATER_DEPTH_CM = "water_depth_cm"
CONF_WATER_TYPE = "water_type"
CONF_TANK_PROFILE = "tank_profile"
CONF_TEMPERATURE_ENTITY = "temperature_entity"
CONF_SPECIES_LIBRARY_URL = "species_library_url"
CONF_GITHUB_TOKEN = "github_token"
CONF_BACKUP_HEATER_SWITCH = "backup_heater_switch"
CONF_BACKUP_HEATER_AUTOMATION_ENABLED = "backup_heater_automation_enabled"
CONF_BACKUP_HEATER_ON_TEMP_C = "backup_heater_on_temp_c"
CONF_BACKUP_HEATER_OFF_TEMP_C = "backup_heater_off_temp_c"
CONF_BACKUP_HEATER_LOW_DELAY_MIN = "backup_heater_low_delay_min"
CONF_BACKUP_HEATER_MAX_RUNTIME_MIN = "backup_heater_max_runtime_min"
CONF_WATER_TEST_NOTIFY_ENTITY = "water_test_notify_entity"

DEFAULT_BACKUP_HEATER_AUTOMATION_ENABLED = True
DEFAULT_BACKUP_HEATER_ON_TEMP_C = 23.0
DEFAULT_BACKUP_HEATER_OFF_TEMP_C = 24.0
DEFAULT_BACKUP_HEATER_LOW_DELAY_MIN = 10
DEFAULT_BACKUP_HEATER_MAX_RUNTIME_MIN = 120

CONF_LIGHT_1_SWITCH = "light_1_switch"
CONF_LIGHT_2_SWITCH = "light_2_switch"
CONF_CO2_SWITCH = "co2_switch"
CONF_LIGHTING_AUTOMATION_ENABLED = "lighting_automation_enabled"
CONF_LIGHTS_ON_TIME = "lights_on_time"
CONF_LIGHT_DURATION_HOURS = "light_duration_hours"
CONF_HALF_LIGHT_DURATION_MIN = "half_light_duration_min"
CONF_BLUE_LIGHT_DURATION_MIN = "blue_light_duration_min"
CONF_CO2_LEAD_MIN = "co2_lead_min"
CONF_CO2_OFF_BEFORE_END_MIN = "co2_off_before_end_min"
CONF_LIGHT_MODE_TOGGLE_SECONDS = "light_mode_toggle_seconds"

DEFAULT_LIGHTING_AUTOMATION_ENABLED = True
DEFAULT_LIGHTS_ON_TIME = "12:00:00"
DEFAULT_LIGHT_DURATION_HOURS = 8.0
DEFAULT_HALF_LIGHT_DURATION_MIN = 120
DEFAULT_BLUE_LIGHT_DURATION_MIN = 30
DEFAULT_CO2_LEAD_MIN = 120
DEFAULT_CO2_OFF_BEFORE_END_MIN = 60
DEFAULT_LIGHT_MODE_TOGGLE_SECONDS = 1.0

WATER_TYPE_FRESHWATER = "freshwater"
WATER_TYPE_MARINE = "marine"

TANK_PROFILE_COMMUNITY = "community"
TANK_PROFILE_SHRIMP = "shrimp"

LIVESTOCK_TYPE_FISH = "fish"
LIVESTOCK_TYPE_SHRIMP = "shrimp"
LIVESTOCK_TYPE_SNAIL = "snail"
LIVESTOCK_TYPE_OTHER = "other"

MANUFACTURER = "Aquarium Manager"
MODEL = "Virtual Aquarium"

SERVICE_ADD_STOCK = "add_stock"
SERVICE_UPDATE_STOCK = "update_stock"
SERVICE_REMOVE_STOCK = "remove_stock"
SERVICE_REFRESH_LIBRARY = "refresh_species_library"
SERVICE_SUBMIT_WATER_READINGS = "submit_water_readings"
SERVICE_SEARCH_SPECIES = "search_species"
SERVICE_ADD_SPECIES_TO_LIBRARY = "add_species_to_library"
SERVICE_RESYNC_LIGHTING = "resync_lighting"
SERVICE_UPDATE_LIGHTING_SCHEDULE = "update_lighting_schedule"
SERVICE_START_WATER_TEST_TIMER = "start_water_test_timer"
SERVICE_UPDATE_FILTER = "update_filter"
SERVICE_MARK_FILTER_CLEANED = "mark_filter_cleaned"

ATTR_ENTRY_ID = "entry_id"
ATTR_STOCK_ID = "stock_id"
ATTR_NAME = "name"
ATTR_QUANTITY = "quantity"
ATTR_SCIENTIFIC_NAME = "scientific_name"
ATTR_NOTES = "notes"
ATTR_AMMONIA = "ammonia"
ATTR_NITRITE = "nitrite"
ATTR_NITRATE = "nitrate"
ATTR_PH = "ph"
ATTR_GH = "gh"
ATTR_KH = "kh"
ATTR_TDS = "tds"
ATTR_LIVESTOCK_TYPE = "livestock_type"
ATTR_TEST_TYPE = "test_type"

# Fields that can be populated from the species library and then overridden by
# the user on an individual stocking record.
PROFILE_FIELDS = (
    "scientific_name",
    "adult_size_cm",
    "temperature_min",
    "temperature_max",
    "ph_min",
    "ph_max",
    "gh_min",
    "gh_max",
    "kh_min",
    "kh_max",
    "tds_min",
    "tds_max",
    "min_group_size",
    "min_tank_length_cm",
    "min_tank_width_cm",
    "min_tank_height_cm",
    "min_tank_volume_l",
    "social_type",
    "temperament",
    "swimming_zone",
    "compatibility",
    "difficulty",
    "diet_type",
    "environment_type",
    "ecology",
    "climate",
    "distribution",
)

STOCK_STORAGE_VERSION = 1
STOCK_STORAGE_KEY_PREFIX = "aquarium_manager.stocking"
LIBRARY_STORAGE_VERSION = 1
LIBRARY_STORAGE_KEY_PREFIX = "aquarium_manager.species_library"

LIBRARY_REFRESH_HOURS = 24
LIBRARY_SCHEMA_VERSION = 1
LIBRARY_MAX_BYTES = 5 * 1024 * 1024
LIBRARY_MAX_SPECIES = 5000

SIGNAL_STOCKING_UPDATED = f"{DOMAIN}_stocking_updated"
SIGNAL_LIBRARY_UPDATED = f"{DOMAIN}_library_updated"
SIGNAL_HEATER_UPDATED = f"{DOMAIN}_heater_updated"
SIGNAL_LIGHTING_UPDATED = f"{DOMAIN}_lighting_updated"
SIGNAL_FILTER_UPDATED = f"{DOMAIN}_filter_updated"
