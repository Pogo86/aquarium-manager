"""Calculated sensors for Aquarium Manager."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquariumManagerConfigEntry
from .filtering import filter_summary
from .const import (
    CONF_LENGTH_CM,
    CONF_TANK_NAME,
    CONF_TANK_PROFILE,
    CONF_TEMPERATURE_ENTITY,
    CONF_WATER_DEPTH_CM,
    CONF_WATER_TYPE,
    CONF_WIDTH_CM,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    SIGNAL_LIBRARY_UPDATED,
    SIGNAL_HEATER_UPDATED,
    SIGNAL_LIGHTING_UPDATED,
    SIGNAL_FILTER_UPDATED,
    SIGNAL_STOCKING_UPDATED,
    TANK_PROFILE_SHRIMP,
    LIVESTOCK_TYPE_FISH,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquariumManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Aquarium Manager sensors."""
    entities: list[SensorEntity] = [
        AquariumVolumeSensor(entry),
        AquariumDimensionsSensor(entry),
        AquariumStockingSensor(entry),
        AquariumLibrarySensor(entry),
        AquariumCommunitySensor(entry),
        AquariumCompatibilitySensor(entry),
        AquariumWaterStatusSensor(entry),
        AquariumBackupHeaterAutomationSensor(entry),
        AquariumLightingAutomationSensor(entry),
        AquariumFilterSensor(entry),
        AquariumDeltaSensor(
            entry,
            key="gh_delta",
            name="GH above source water",
            tank_key="gh",
            source_key="source_gh",
            unit="dGH",
            icon="mdi:delta",
        ),
        AquariumDeltaSensor(
            entry,
            key="kh_delta",
            name="KH above source water",
            tank_key="kh",
            source_key="source_kh",
            unit="dKH",
            icon="mdi:delta",
        ),
        AquariumDeltaSensor(
            entry,
            key="nitrate_delta",
            name="Nitrate above source water",
            tank_key="nitrate",
            source_key="source_nitrate",
            unit="mg/L",
            icon="mdi:delta",
        ),
    ]

    if entry.data.get(CONF_TEMPERATURE_ENTITY):
        entities.append(AquariumTemperatureSensor(entry))

    async_add_entities(entities)


class AquariumBaseSensor(SensorEntity):
    """Common aquarium sensor base."""

    _attr_has_entity_name = True

    def __init__(self, entry: AquariumManagerConfigEntry, key: str) -> None:
        """Initialize common fields."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_TANK_NAME],
            manufacturer=MANUFACTURER,
            model=MODEL,
        )


class AquariumVolumeSensor(AquariumBaseSensor):
    """Calculated aquarium water volume."""

    _attr_name = "Calculated volume"
    _attr_icon = "mdi:fishbowl-outline"
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "calculated_volume")

    @property
    def native_value(self) -> float:
        length = float(self._entry.data[CONF_LENGTH_CM])
        width = float(self._entry.data[CONF_WIDTH_CM])
        depth = float(self._entry.data[CONF_WATER_DEPTH_CM])
        return round((length * width * depth) / 1000, 1)

    @property
    def extra_state_attributes(self) -> dict[str, float]:
        return {
            "length_cm": float(self._entry.data[CONF_LENGTH_CM]),
            "width_cm": float(self._entry.data[CONF_WIDTH_CM]),
            "water_depth_cm": float(self._entry.data[CONF_WATER_DEPTH_CM]),
        }


class AquariumDimensionsSensor(AquariumBaseSensor):
    """Human-readable aquarium dimensions."""

    _attr_name = "Dimensions"
    _attr_icon = "mdi:ruler-square"

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "dimensions")

    @property
    def native_value(self) -> str:
        length = self._entry.data[CONF_LENGTH_CM]
        width = self._entry.data[CONF_WIDTH_CM]
        depth = self._entry.data[CONF_WATER_DEPTH_CM]
        return f"{length} × {width} × {depth} cm"

    @property
    def extra_state_attributes(self) -> dict[str, float]:
        return {
            "length_cm": float(self._entry.data[CONF_LENGTH_CM]),
            "width_cm": float(self._entry.data[CONF_WIDTH_CM]),
            "water_depth_cm": float(self._entry.data[CONF_WATER_DEPTH_CM]),
        }



BIOLOAD_DIVISOR = 100.0
MAX_COMPATIBLE_RESULTS = 100


def _estimated_bioload_points(
    adult_size_cm: Any,
    quantity: int = 1,
) -> float | None:
    """Return a relative adult-size-based bioload estimate.

    This deliberately is not a stocking percentage. Fish biomass/waste broadly
    increases with body volume, so adult length cubed is useful as a relative
    planning signal, but species shape, diet and husbandry can change real waste
    production substantially.
    """
    if adult_size_cm is None:
        return None
    try:
        size = float(adult_size_cm)
        count = max(0, int(quantity))
    except (TypeError, ValueError):
        return None
    if size <= 0 or count <= 0:
        return 0.0
    return round(((size ** 3) / BIOLOAD_DIVISOR) * count, 1)


def _ranges_overlap(
    first_min: float,
    first_max: float,
    second_min: float,
    second_max: float,
) -> bool:
    """Return True when two inclusive numeric ranges overlap."""
    return max(first_min, second_min) <= min(first_max, second_max)


def _group_recommendations(
    entry: AquariumManagerConfigEntry,
    stocking: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return structured minimum-group shortfalls for current livestock."""
    result: list[dict[str, Any]] = []
    tank_dimensions = {
        "length": float(entry.data[CONF_LENGTH_CM]),
        "width": float(entry.data[CONF_WIDTH_CM]),
        "height": float(entry.data[CONF_WATER_DEPTH_CM]),
    }
    tank_volume = _tank_volume_l(entry)

    for item in stocking:
        if str(item.get("livestock_type") or LIVESTOCK_TYPE_FISH) != LIVESTOCK_TYPE_FISH:
            continue
        minimum = item.get("min_group_size")
        if minimum is None:
            continue
        try:
            minimum_int = int(minimum)
            quantity = max(0, int(item.get("quantity") or 0))
        except (TypeError, ValueError):
            continue
        if quantity >= minimum_int:
            continue

        add_count = minimum_int - quantity
        blockers: list[str] = []
        for dimension, key in (
            ("length", "min_tank_length_cm"),
            ("width", "min_tank_width_cm"),
            ("height", "min_tank_height_cm"),
        ):
            required = item.get(key)
            if required is not None and tank_dimensions[dimension] < float(required):
                blockers.append(
                    f"tank {dimension} is below {float(required):g} cm"
                )
        min_volume = item.get("min_tank_volume_l")
        if min_volume is not None and tank_volume < float(min_volume):
            blockers.append(f"tank is below about {float(min_volume):g} L")

        result.append(
            {
                "stock_id": str(item.get("id") or ""),
                "name": str(item.get("name") or "Unnamed"),
                "scientific_name": str(item.get("scientific_name") or ""),
                "quantity": quantity,
                "minimum_group": minimum_int,
                "add_count": add_count,
                "target_quantity": minimum_int,
                "safe_to_add": not blockers,
                "blocking_reason": "; ".join(blockers),
                "estimated_added_bioload_points": _estimated_bioload_points(
                    item.get("adult_size_cm"),
                    add_count,
                ),
            }
        )
    return result


def _tank_volume_l(entry: AquariumManagerConfigEntry) -> float:
    """Return configured geometric water-column volume."""
    return (
        float(entry.data[CONF_LENGTH_CM])
        * float(entry.data[CONF_WIDTH_CM])
        * float(entry.data[CONF_WATER_DEPTH_CM])
    ) / 1000.0


def _relative_bioload_summary(
    entry: AquariumManagerConfigEntry,
    stocking: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarise the relative adult-size bioload index and its data coverage."""
    total_points = 0.0
    known_individuals = 0
    unknown_individuals = 0
    unknown_groups: list[str] = []

    for item in stocking:
        if str(item.get("livestock_type") or LIVESTOCK_TYPE_FISH) != LIVESTOCK_TYPE_FISH:
            continue
        try:
            quantity = max(0, int(item.get("quantity") or 0))
        except (TypeError, ValueError):
            quantity = 0

        adult_size = item.get("adult_size_cm")
        points = _estimated_bioload_points(adult_size, quantity)
        if adult_size is None or points is None:
            unknown_individuals += quantity
            if quantity:
                unknown_groups.append(str(item.get("name") or "Unnamed"))
            continue

        try:
            if float(adult_size) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            unknown_individuals += quantity
            if quantity:
                unknown_groups.append(str(item.get("name") or "Unnamed"))
            continue

        known_individuals += quantity
        total_points += float(points or 0)

    total_individuals = known_individuals + unknown_individuals
    coverage = (known_individuals / total_individuals * 100.0) if total_individuals else 100.0
    volume = _tank_volume_l(entry)
    density = (total_points / volume) if volume > 0 else None

    recommendations = _group_recommendations(entry, stocking)
    safe_added_points = sum(
        float(item.get("estimated_added_bioload_points") or 0)
        for item in recommendations
        if item.get("safe_to_add")
    )

    non_fish_individuals = sum(
        max(0, int(item.get("quantity") or 0))
        for item in stocking
        if str(item.get("livestock_type") or LIVESTOCK_TYPE_FISH) != LIVESTOCK_TYPE_FISH
    )

    return {
        "points": round(total_points, 1),
        "points_per_litre": round(density, 2) if density is not None else None,
        "coverage_percent": round(coverage, 0),
        "known_individuals": known_individuals,
        "unknown_individuals": unknown_individuals,
        "unknown_groups": unknown_groups,
        "non_fish_individuals_excluded": non_fish_individuals,
        "projected_points_after_safe_group_fixes": round(total_points + safe_added_points, 1),
        "method": "Relative index: quantity × adult length³ ÷ 100",
    }


def _stocking_assessment(
    hass: HomeAssistant,
    entry: AquariumManagerConfigEntry,
    stocking: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assess concrete stocking constraints without inventing a maximum bioload."""
    bioload = _relative_bioload_summary(entry, stocking)
    ranges = _community_ranges_for_stock(stocking)
    group_recommendations = _group_recommendations(entry, stocking)

    issues: list[dict[str, str]] = []
    tank_issues: list[str] = []
    profile_gaps: list[str] = []

    for item in stocking:
        for warning in _stock_warnings(entry, item):
            warning_type = str(warning.get("type") or "")
            message = str(warning.get("message") or "")
            if warning_type == "tank_size":
                tank_issues.append(message)
            elif warning_type == "profile_missing":
                profile_gaps.append(message)

    conflicts = [
        key for key, value in ranges.items() if value.get("conflict")
    ]
    if conflicts:
        issues.append({
            "severity": "danger",
            "message": "Current livestock have no shared "
            + ", ".join(key.upper() if key in {"gh", "kh"} else key for key in conflicts)
            + " range.",
        })

    for message in tank_issues:
        issues.append({"severity": "warning", "message": message})

    current_values = {
        "temperature": _current_temperature(hass, entry),
        "ph": entry.runtime_data.readings.get("ph"),
        "gh": entry.runtime_data.readings.get("gh"),
        "kh": entry.runtime_data.readings.get("kh"),
        "tds": entry.runtime_data.readings.get("tds"),
    }
    labels = {
        "temperature": ("temperature", "°C"),
        "ph": ("pH", ""),
        "gh": ("GH", " dGH"),
        "kh": ("KH", " dKH"),
        "tds": ("TDS", " ppm"),
    }
    water_issues: list[str] = []
    for key, current in current_values.items():
        care_range = ranges.get(key, {})
        if current is None or care_range.get("conflict"):
            continue
        minimum = care_range.get("min")
        maximum = care_range.get("max")
        if minimum is None or maximum is None:
            continue
        current_float = float(current)
        if current_float < float(minimum) or current_float > float(maximum):
            label, unit = labels[key]
            message = (
                f"Current {label} {current_float:g}{unit} is outside the community "
                f"overlap {float(minimum):g}–{float(maximum):g}{unit}."
            )
            water_issues.append(message)
            issues.append({"severity": "warning", "message": message})

    tank_volume_l = _tank_volume_l(entry)
    filtration = filter_summary(entry.runtime_data.filter_data, tank_volume_l)
    if filtration["sizing_status"] == "above_manufacturer_range":
        issues.append({
            "severity": "warning",
            "message": (
                f"Filter is rated up to {filtration['rated_tank_max_l']:g} L, below this "
                f"aquarium's {tank_volume_l:g} L calculated water volume."
            ),
        })
    if filtration["flow_status"] == "low":
        issues.append({
            "severity": "warning",
            "message": (
                f"Filter turnover is about {filtration['turnover_per_hour']:g}×/h, "
                "which is low for circulation and distribution."
            ),
        })
    if not filtration["biological_media_present"]:
        issues.append({
            "severity": "warning",
            "message": "No biological filter media is recorded.",
        })
    if filtration["maintenance_status"] == "overdue":
        issues.append({
            "severity": "warning",
            "message": "Filter maintenance is overdue.",
        })

    safe_group = [item for item in group_recommendations if item.get("safe_to_add")]
    blocked_group = [item for item in group_recommendations if not item.get("safe_to_add")]

    for item in safe_group:
        issues.append({
            "severity": "warning",
            "message": (
                f"{item['name']}: add {item['add_count']} to reach the recorded "
                f"minimum group of {item['minimum_group']}."
            ),
        })
    for item in blocked_group:
        issues.append({
            "severity": "warning",
            "message": (
                f"{item['name']} is below its recorded minimum group, but do not add "
                f"more yet: {item.get('blocking_reason') or 'tank requirement not met'}."
            ),
        })

    if bioload["unknown_groups"]:
        issues.append({
            "severity": "info",
            "message": "Relative bioload excludes fish without an adult-size value: "
            + ", ".join(bioload["unknown_groups"])
            + ".",
        })

    if not stocking:
        status = "empty"
        label = "No livestock recorded"
        main_constraint = "None"
    elif conflicts:
        status = "danger"
        label = "Community range conflict"
        main_constraint = "Water compatibility"
    elif tank_issues or blocked_group:
        status = "attention"
        label = "Tank constraint needs attention"
        main_constraint = "Tank dimensions / volume"
    elif water_issues:
        status = "attention"
        label = "Current water needs attention"
        main_constraint = "Current water parameters"
    elif safe_group:
        status = "attention"
        label = "Group-size improvements suggested"
        main_constraint = "Group sizes"
    elif (
        filtration["sizing_status"] == "above_manufacturer_range"
        or filtration["flow_status"] == "low"
        or not filtration["biological_media_present"]
        or filtration["maintenance_status"] == "overdue"
    ):
        status = "attention"
        label = "Filtration needs attention"
        main_constraint = "Filtration"
    elif bioload["coverage_percent"] < 100 or profile_gaps:
        status = "incomplete"
        label = "Assessment incomplete"
        main_constraint = "Missing species data"
    else:
        status = "good"
        label = "No structural issues detected"
        main_constraint = "None detected"

    return {
        "status": status,
        "label": label,
        "main_constraint": main_constraint,
        "relative_bioload": bioload,
        "tank_volume_l": round(_tank_volume_l(entry), 1),
        "tank_issue_count": len(tank_issues),
        "group_shortfall_count": len(group_recommendations),
        "safe_group_fix_count": len(safe_group),
        "blocked_group_fix_count": len(blocked_group),
        "community_conflicts": conflicts,
        "current_water_issue_count": len(water_issues),
        "issues": issues,
        "filtration_status": (
            "attention"
            if filtration["sizing_status"] == "above_manufacturer_range"
            or filtration["flow_status"] == "low"
            or not filtration["biological_media_present"]
            or filtration["maintenance_status"] == "overdue"
            else "good"
        ),
        "filtration_note": (
            f"{filtration['manufacturer']} {filtration['model']} · "
            + (
                f"{filtration['turnover_per_hour']:g}×/h "
                f"({filtration['flow_source']} flow)"
                if filtration["turnover_per_hour"] is not None
                else "turnover unavailable"
            )
        ),
        "filtration": filtration,
        "bioload_note": (
            "Relative bioload is a comparison index, not a safe maximum or stocking percentage."
        ),
    }


def _current_temperature(
    hass: HomeAssistant,
    entry: AquariumManagerConfigEntry,
) -> float | None:
    entity_id = entry.data.get(CONF_TEMPERATURE_ENTITY)
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in {"unknown", "unavailable"}:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def _community_ranges_for_stock(
    stocking: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        "temperature": _range_for(
            stocking, "temperature_min", "temperature_max", "°C"
        ),
        "ph": _range_for(stocking, "ph_min", "ph_max", ""),
        "gh": _range_for(stocking, "gh_min", "gh_max", "dGH"),
        "kh": _range_for(stocking, "kh_min", "kh_max", "dKH"),
        "tds": _range_for(stocking, "tds_min", "tds_max", "ppm"),
    }


def _candidate_assessment(
    hass: HomeAssistant,
    entry: AquariumManagerConfigEntry,
    profile: dict[str, Any],
    stocking: list[dict[str, Any]],
    community_ranges: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Assess one unstocked library profile against this aquarium."""
    failures: list[str] = []
    cautions: list[str] = []
    matches: list[str] = []
    score = 100

    tank_dimensions = {
        "length": float(entry.data[CONF_LENGTH_CM]),
        "width": float(entry.data[CONF_WIDTH_CM]),
        "height": float(entry.data[CONF_WATER_DEPTH_CM]),
    }
    minimums = {
        "length": profile.get("min_tank_length_cm"),
        "width": profile.get("min_tank_width_cm"),
        "height": profile.get("min_tank_height_cm"),
    }
    for dimension, minimum in minimums.items():
        if minimum is None:
            continue
        if tank_dimensions[dimension] < float(minimum):
            failures.append(
                f"Needs at least {float(minimum):g} cm tank {dimension}"
            )

    min_volume = profile.get("min_tank_volume_l")
    if min_volume is not None and _tank_volume_l(entry) < float(min_volume):
        failures.append(
            f"Needs about {float(min_volume):g} L minimum"
        )

    water_type = str(entry.data.get(CONF_WATER_TYPE) or "").casefold()
    environment_type = str(profile.get("environment_type") or "").casefold()
    if water_type == "freshwater" and environment_type and "freshwater" not in environment_type:
        failures.append("Not listed as a freshwater species")

    current_values = {
        "temperature": _current_temperature(hass, entry),
        "ph": entry.runtime_data.readings.get("ph"),
        "gh": entry.runtime_data.readings.get("gh"),
        "kh": entry.runtime_data.readings.get("kh"),
    }
    range_keys = {
        "temperature": ("temperature_min", "temperature_max", "temperature", "°C"),
        "ph": ("ph_min", "ph_max", "pH", ""),
        "gh": ("gh_min", "gh_max", "GH", " dGH"),
        "kh": ("kh_min", "kh_max", "KH", " dKH"),
    }

    for key, (minimum_key, maximum_key, label, unit) in range_keys.items():
        candidate_min = profile.get(minimum_key)
        candidate_max = profile.get(maximum_key)
        community = community_ranges[key]

        if candidate_min is None or candidate_max is None:
            score -= 8
            cautions.append(f"No {label} range in the library")
            continue

        candidate_min = float(candidate_min)
        candidate_max = float(candidate_max)

        if (
            community.get("min") is not None
            and community.get("max") is not None
            and not community.get("conflict")
        ):
            if not _ranges_overlap(
                candidate_min,
                candidate_max,
                float(community["min"]),
                float(community["max"]),
            ):
                failures.append(
                    f"{label} range does not overlap the current community"
                )
            else:
                matches.append(f"{label} overlaps community range")

        current = current_values[key]
        if current is not None:
            current_float = float(current)
            if current_float < candidate_min or current_float > candidate_max:
                failures.append(
                    f"Current {label} {current_float:g}{unit} is outside "
                    f"{candidate_min:g}–{candidate_max:g}{unit}"
                )
            else:
                matches.append(f"Current {label} is within range")

    temperament = str(profile.get("temperament") or "")
    temperament_lower = temperament.casefold()
    caution_words = ("aggressive", "fin-nip", "fin nip", "territorial", "fights")
    if any(word in temperament_lower for word in caution_words):
        score -= 12
        cautions.append(
            f"Temperament needs checking: {temperament}"
        )

    compatibility = str(profile.get("compatibility") or "")
    if compatibility and "not a community" in compatibility.casefold():
        failures.append("Profile says it is not suitable for a general community")

    minimum_group = profile.get("min_group_size")
    suggested_group = int(minimum_group) if minimum_group is not None else 1
    if minimum_group is None:
        score -= 4

    adult_size = profile.get("adult_size_cm")
    group_bioload = _estimated_bioload_points(adult_size, suggested_group)

    score = max(0, min(100, score))
    confidence = "high" if score >= 85 else "medium" if score >= 65 else "limited"

    return {
        "compatible": not failures,
        "score": score,
        "confidence": confidence,
        "failures": failures,
        "cautions": cautions,
        "matches": matches,
        "suggested_group": suggested_group,
        "estimated_group_bioload_points": group_bioload,
    }


def _stock_warnings(
    entry: AquariumManagerConfigEntry,
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return warnings specific to one stock record."""
    warnings: list[dict[str, str]] = []
    livestock_type = str(item.get("livestock_type") or LIVESTOCK_TYPE_FISH)
    name = str(item.get("name") or ("Fish" if livestock_type == LIVESTOCK_TYPE_FISH else "Livestock"))
    quantity = int(item.get("quantity") or 0)
    minimum_group = item.get("min_group_size")

    if livestock_type == LIVESTOCK_TYPE_FISH and minimum_group is not None and quantity < int(minimum_group):
        social = str(item.get("social_type") or "group")
        add_count = int(minimum_group) - quantity
        warnings.append(
            {
                "type": "group_size",
                "severity": "warning",
                "quantity": quantity,
                "minimum_group": int(minimum_group),
                "add_count": add_count,
                "message": (
                    f"{name}: only {quantity} kept; recommended minimum is "
                    f"{int(minimum_group)} ({social}). Add {add_count} to reach "
                    f"the minimum."
                ),
            }
        )

    tank_dimensions = {
        "length": float(entry.data[CONF_LENGTH_CM]),
        "width": float(entry.data[CONF_WIDTH_CM]),
        "height": float(entry.data[CONF_WATER_DEPTH_CM]),
    }
    minimums = {
        "length": item.get("min_tank_length_cm"),
        "width": item.get("min_tank_width_cm"),
        "height": item.get("min_tank_height_cm"),
    }

    shortfalls: list[str] = []
    for dimension, minimum in minimums.items():
        if minimum is not None and tank_dimensions[dimension] < float(minimum):
            shortfalls.append(
                f"{dimension} {tank_dimensions[dimension]:g} cm < {float(minimum):g} cm"
            )

    if shortfalls:
        warnings.append(
            {
                "type": "tank_size",
                "severity": "warning",
                "message": f"{name}: tank is below the profile recommendation ({'; '.join(shortfalls)}).",
            }
        )

    if not item.get("profile_id"):
        has_manual_ranges = any(
            item.get(key) is not None
            for key in (
                "temperature_min", "temperature_max", "ph_min", "ph_max",
                "gh_min", "gh_max", "kh_min", "kh_max", "tds_min", "tds_max"
            )
        )
        warnings.append(
            {
                "type": "profile_missing",
                "severity": "info",
                "message": (
                    f"{name}: no built-in species profile is attached; manually entered parameters are used where available."
                    if has_manual_ranges
                    else f"{name}: no species profile is attached, so it is excluded from community ranges."
                ),
            }
        )

    return warnings


def _range_for(
    stocking: list[dict[str, Any]],
    minimum_key: str,
    maximum_key: str,
    unit: str,
) -> dict[str, Any]:
    """Calculate the common overlap for a care parameter."""
    contributors: list[dict[str, Any]] = []
    excluded: list[str] = []

    for item in stocking:
        minimum = item.get(minimum_key)
        maximum = item.get(maximum_key)
        if minimum is None or maximum is None:
            excluded.append(str(item.get("name") or "Unnamed"))
            continue
        contributors.append(
            {
                "name": str(item.get("name") or "Unnamed"),
                "min": float(minimum),
                "max": float(maximum),
            }
        )

    if not contributors:
        return {
            "min": None,
            "max": None,
            "unit": unit,
            "conflict": False,
            "contributors": [],
            "excluded": excluded,
        }

    common_min = max(item["min"] for item in contributors)
    common_max = min(item["max"] for item in contributors)

    return {
        "min": common_min,
        "max": common_max,
        "unit": unit,
        "conflict": common_min > common_max,
        "contributors": [item["name"] for item in contributors],
        "excluded": excluded,
    }


class AquariumStockingSensor(AquariumBaseSensor):
    """Expose stored aquarium livestock."""

    _attr_name = "Stocking"
    _attr_icon = "mdi:fish"

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "stocking")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_STOCKING_UPDATED}_{self._entry.entry_id}",
                self._handle_stocking_update,
            )
        )

    def _handle_stocking_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return sum(
            max(0, int(item.get("quantity", 0)))
            for item in self._entry.runtime_data.stocking
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        stocking: list[dict[str, Any]] = []
        for item in self._entry.runtime_data.stocking:
            exposed = dict(item)
            exposed["warnings"] = _stock_warnings(self._entry, item)
            exposed["bioload_points_each"] = _estimated_bioload_points(
                item.get("adult_size_cm"), 1
            )
            exposed["bioload_points_group"] = _estimated_bioload_points(
                item.get("adult_size_cm"),
                int(item.get("quantity") or 0),
            )
            exposed["relative_bioload_points_each"] = exposed["bioload_points_each"]
            exposed["relative_bioload_points_group"] = exposed["bioload_points_group"]
            stocking.append(exposed)

        bioload = _relative_bioload_summary(
            self._entry,
            self._entry.runtime_data.stocking,
        )
        assessment = _stocking_assessment(
            self.hass,
            self._entry,
            self._entry.runtime_data.stocking,
        )

        return {
            "config_entry_id": self._entry.entry_id,
            "species_count": len(stocking),
            # Keep the original attribute names for backwards compatibility.
            "estimated_bioload_points_total": bioload["points"],
            "bioload_method": bioload["method"],
            "relative_bioload_points_total": bioload["points"],
            "relative_bioload_points_per_l": bioload["points_per_litre"],
            "relative_bioload_coverage_percent": bioload["coverage_percent"],
            "relative_bioload": bioload,
            "stocking_assessment": assessment,
            "group_recommendations": _group_recommendations(
                self._entry,
                self._entry.runtime_data.stocking,
            ),
            "stocking": stocking,
            "species_library": (
                self._entry.runtime_data.library.library_summary()
                if self._entry.runtime_data.library is not None
                else []
            ),
            "library": (
                self._entry.runtime_data.library.status
                if self._entry.runtime_data.library is not None
                else {}
            ),
        }


class AquariumLibrarySensor(AquariumBaseSensor):
    """Expose fish-library source and refresh status."""

    _attr_name = "Fish library"
    _attr_icon = "mdi:bookshelf"

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "fish_library")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_LIBRARY_UPDATED}_{self._entry.entry_id}",
                self._handle_update,
            )
        )

    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        library = self._entry.runtime_data.library
        return len(library.profiles) if library is not None else 0

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        library = self._entry.runtime_data.library
        if library is None:
            return {}
        attrs = dict(library.status)
        attrs["github_write_ready"] = self._entry.runtime_data.github_write_ready
        return attrs


class AquariumCommunitySensor(AquariumBaseSensor):
    """Calculate common ranges and warnings across the aquarium community."""

    _attr_name = "Community"
    _attr_icon = "mdi:account-group-outline"
    _attr_should_poll = True

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "community")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_STOCKING_UPDATED}_{self._entry.entry_id}",
                self._handle_update,
            )
        )

    def _handle_update(self) -> None:
        self.async_write_ha_state()

    def _ranges(self) -> dict[str, dict[str, Any]]:
        return _community_ranges_for_stock(self._entry.runtime_data.stocking)

    def _current_temperature(self) -> float | None:
        return _current_temperature(self.hass, self._entry)

    def _warnings(self, ranges: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        stocking = self._entry.runtime_data.stocking

        for item in stocking:
            for warning in _stock_warnings(self._entry, item):
                warnings.append({"stock_id": str(item.get("id") or ""), **warning})

        labels = {
            "temperature": "temperature",
            "ph": "pH",
            "gh": "GH",
            "kh": "KH",
        }
        for key, range_data in ranges.items():
            if range_data["conflict"]:
                warnings.append(
                    {
                        "stock_id": "",
                        "type": "range_conflict",
                        "severity": "danger",
                        "message": f"No shared {labels[key]} range exists for all profiled fish.",
                    }
                )

        current_values = {
            "temperature": self._current_temperature(),
            "ph": self._entry.runtime_data.readings.get("ph"),
            "gh": self._entry.runtime_data.readings.get("gh"),
            "kh": self._entry.runtime_data.readings.get("kh"),
        }
        for key, current in current_values.items():
            range_data = ranges[key]
            if (
                current is None
                or range_data["min"] is None
                or range_data["max"] is None
                or range_data["conflict"]
            ):
                continue
            if float(current) < float(range_data["min"]) or float(current) > float(range_data["max"]):
                unit = f" {range_data['unit']}" if range_data["unit"] else ""
                warnings.append(
                    {
                        "stock_id": "",
                        "type": "current_parameter",
                        "severity": "warning",
                        "message": (
                            f"Current {labels[key]} {float(current):g}{unit} is outside the "
                            f"community range {float(range_data['min']):g}–{float(range_data['max']):g}{unit}."
                        ),
                    }
                )

        return warnings

    @property
    def native_value(self) -> str:
        stocking = self._entry.runtime_data.stocking
        if not stocking:
            return "empty"

        ranges = self._ranges()
        warnings = self._warnings(ranges)

        if any(warning["severity"] == "danger" for warning in warnings):
            return "conflict"
        if any(warning["severity"] == "warning" for warning in warnings):
            return "attention"
        if any(
            not item.get("profile_id")
            and not any(
                item.get(min_key) is not None and item.get(max_key) is not None
                for min_key, max_key in (
                    ("temperature_min", "temperature_max"),
                    ("ph_min", "ph_max"),
                    ("gh_min", "gh_max"),
                    ("kh_min", "kh_max"),
                    ("tds_min", "tds_max"),
                )
            )
            for item in stocking
        ):
            return "incomplete"
        return "good"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ranges = self._ranges()
        warnings = self._warnings(ranges)
        stocking = self._entry.runtime_data.stocking

        return {
            "ranges": ranges,
            "warnings": warnings,
            "profiled_groups": sum(1 for item in stocking if item.get("profile_id")),
            "total_groups": len(stocking),
        }



class AquariumCompatibilitySensor(AquariumBaseSensor):
    """Recommend unstocked library fish that fit the current aquarium."""

    _attr_name = "Compatible fish"
    _attr_icon = "mdi:fish-plus"
    _attr_should_poll = True

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "compatible_fish")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_STOCKING_UPDATED}_{self._entry.entry_id}",
                self._handle_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_LIBRARY_UPDATED}_{self._entry.entry_id}",
                self._handle_update,
            )
        )

    def _handle_update(self) -> None:
        self.async_write_ha_state()

    def _build(self) -> tuple[list[dict[str, Any]], int, dict[str, dict[str, Any]]]:
        runtime = self._entry.runtime_data
        library = runtime.library
        if self._entry.data.get(CONF_TANK_PROFILE) == TANK_PROFILE_SHRIMP:
            return [], 0, _community_ranges_for_stock(runtime.stocking)
        if library is None:
            return [], 0, _community_ranges_for_stock(runtime.stocking)

        stocking = runtime.stocking
        stocked_profile_ids = {
            str(item.get("profile_id"))
            for item in stocking
            if item.get("profile_id")
        }
        stocked_scientific = {
            str(item.get("scientific_name") or "").casefold()
            for item in stocking
            if item.get("scientific_name")
        }
        community_ranges = _community_ranges_for_stock(stocking)

        # When the current community itself has a hard range conflict, adding a
        # "compatible" species would be misleading. Surface the conflict and
        # wait until the existing stocking is reconciled.
        if any(value.get("conflict") for value in community_ranges.values()):
            return [], 0, community_ranges

        compatible: list[dict[str, Any]] = []
        excluded_count = 0

        for profile in library.profiles.values():
            if profile.get("profile_id") in stocked_profile_ids:
                continue
            scientific = str(profile.get("scientific_name") or "").casefold()
            if scientific and scientific in stocked_scientific:
                continue

            assessment = _candidate_assessment(
                self.hass,
                self._entry,
                profile,
                stocking,
                community_ranges,
            )
            if not assessment["compatible"]:
                excluded_count += 1
                continue

            candidate = {
                "profile_id": profile.get("profile_id"),
                "name": profile.get("name"),
                "scientific_name": profile.get("scientific_name"),
                "adult_size_cm": profile.get("adult_size_cm"),
                "temperature_min": profile.get("temperature_min"),
                "temperature_max": profile.get("temperature_max"),
                "ph_min": profile.get("ph_min"),
                "ph_max": profile.get("ph_max"),
                "gh_min": profile.get("gh_min"),
                "gh_max": profile.get("gh_max"),
                "kh_min": profile.get("kh_min"),
                "kh_max": profile.get("kh_max"),
                "min_group_size": profile.get("min_group_size"),
                "min_tank_length_cm": profile.get("min_tank_length_cm"),
                "min_tank_width_cm": profile.get("min_tank_width_cm"),
                "min_tank_height_cm": profile.get("min_tank_height_cm"),
                "min_tank_volume_l": profile.get("min_tank_volume_l"),
                "social_type": profile.get("social_type"),
                "temperament": profile.get("temperament"),
                "swimming_zone": profile.get("swimming_zone"),
                "compatibility": profile.get("compatibility"),
                "difficulty": profile.get("difficulty"),
                "diet_type": profile.get("diet_type"),
                "environment_type": profile.get("environment_type"),
                "ecology": profile.get("ecology"),
                "climate": profile.get("climate"),
                "distribution": profile.get("distribution"),
                "care_notes": list(profile.get("cautions", [])),
                "source_label": profile.get("source_label"),
                "source_url": profile.get("source_url"),
                **assessment,
            }
            compatible.append(candidate)

        compatible.sort(
            key=lambda item: (
                -int(item.get("score") or 0),
                str(item.get("name") or "").casefold(),
            )
        )
        return compatible, excluded_count, community_ranges

    @property
    def native_value(self) -> int:
        compatible, _, _ = self._build()
        return len(compatible)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        compatible, excluded_count, community_ranges = self._build()
        stocking = self._entry.runtime_data.stocking
        recommendations = _group_recommendations(self._entry, stocking)
        total_bioload = round(
            sum(
                float(
                    _estimated_bioload_points(
                        item.get("adult_size_cm"),
                        int(item.get("quantity") or 0),
                    )
                    or 0
                )
                for item in stocking
                if str(item.get("livestock_type") or LIVESTOCK_TYPE_FISH) == LIVESTOCK_TYPE_FISH
            ),
            1,
        )
        current_values = {
            "temperature": _current_temperature(self.hass, self._entry),
            "ph": self._entry.runtime_data.readings.get("ph"),
            "gh": self._entry.runtime_data.readings.get("gh"),
            "kh": self._entry.runtime_data.readings.get("kh"),
        }
        has_conflict = any(
            value.get("conflict") for value in community_ranges.values()
        )

        return {
            "compatible_count": len(compatible),
            "shown_count": min(len(compatible), MAX_COMPATIBLE_RESULTS),
            "compatible": compatible[:MAX_COMPATIBLE_RESULTS],
            "excluded_count": excluded_count,
            "community_conflict": has_conflict,
            "community_ranges": community_ranges,
            "current_values": current_values,
            "group_recommendations": recommendations,
            "estimated_bioload_points_total": total_bioload,
            "bioload_method": "Relative index: quantity × adult length³ ÷ 100",
            "tank_volume_l": round(_tank_volume_l(self._entry), 1),
        }


class AquariumFilterSensor(AquariumBaseSensor):
    """Expose configured filter, turnover, media and maintenance state."""

    _attr_name = "Filter"
    _attr_icon = "mdi:air-filter"

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "filter")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_FILTER_UPDATED}_{self._entry.entry_id}",
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> str:
        data = self._entry.runtime_data.filter_data
        manufacturer = str(data.get("manufacturer") or "").strip()
        model = str(data.get("model") or "").strip()
        return " ".join(part for part in (manufacturer, model) if part) or "Configured"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        volume = _tank_volume_l(self._entry)
        summary = filter_summary(self._entry.runtime_data.filter_data, volume)
        return {
            "config_entry_id": self._entry.entry_id,
            "filter": self._entry.runtime_data.filter_data,
            "summary": summary,
            "tank_volume_l": round(volume, 1),
            **summary,
        }



class AquariumBackupHeaterAutomationSensor(AquariumBaseSensor):
    """Expose backup-heater controller status and diagnostics."""

    _attr_name = "Backup heater automation"
    _attr_icon = "mdi:radiator-disabled"

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "backup_heater_automation")

    @property
    def native_value(self) -> str:
        controller = self._entry.runtime_data.heater_controller
        if controller is None:
            return "not_configured"
        return controller.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        controller = self._entry.runtime_data.heater_controller
        if controller is None:
            return {"enabled": False, "reason": "Temperature sensor or backup heater not configured"}
        return controller.status_attributes

    @property
    def icon(self) -> str:
        state = self.native_value
        return {
            "heating": "mdi:radiator",
            "waiting": "mdi:timer-sand",
            "standby": "mdi:radiator-disabled",
            "lockout": "mdi:alert-octagon-outline",
            "sensor_unavailable": "mdi:thermometer-alert",
            "switch_unavailable": "mdi:power-plug-off-outline",
            "disabled": "mdi:toggle-switch-off-outline",
        }.get(state, "mdi:radiator-disabled")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_HEATER_UPDATED}_{self._entry.entry_id}",
                self.async_write_ha_state,
            )
        )



class AquariumLightingAutomationSensor(AquariumBaseSensor):
    """Expose calculated lighting and CO2 schedule status."""

    _attr_name = "Lighting automation"
    _attr_icon = "mdi:lightbulb-on-outline"

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "lighting_automation")

    @property
    def native_value(self) -> str:
        controller = self._entry.runtime_data.lighting_controller
        if controller is None:
            return "not_configured"
        return controller.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        controller = self._entry.runtime_data.lighting_controller
        if controller is None:
            return {
                "enabled": False,
                "reason": "Two light switches and a CO2 switch are not configured",
            }
        return controller.status_attributes

    @property
    def icon(self) -> str:
        return {
            "full": "mdi:weather-sunny",
            "half": "mdi:brightness-6",
            "blue": "mdi:weather-night",
            "co2_precharge": "mdi:molecule-co2",
            "day_off": "mdi:lightbulb-off-outline",
            "off": "mdi:lightbulb-off-outline",
            "disabled": "mdi:toggle-switch-off-outline",
            "not_configured": "mdi:lightbulb-alert-outline",
            "equipment_unavailable": "mdi:power-plug-off-outline",
        }.get(self.native_value, "mdi:lightbulb-on-outline")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_LIGHTING_UPDATED}_{self._entry.entry_id}",
                self.async_write_ha_state,
            )
        )


class AquariumTemperatureSensor(AquariumBaseSensor):
    """Mirror a selected existing temperature sensor into the aquarium device."""

    _attr_name = "Temperature"
    _attr_icon = "mdi:thermometer"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = True

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "temperature")
        self._source_entity_id = entry.data[CONF_TEMPERATURE_ENTITY]

    @property
    def native_value(self) -> float | None:
        state = self.hass.states.get(self._source_entity_id)
        if state is None or state.state in {"unknown", "unavailable"}:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    @property
    def native_unit_of_measurement(self) -> str:
        state = self.hass.states.get(self._source_entity_id)
        if state is not None:
            unit = state.attributes.get("unit_of_measurement")
            if unit:
                return str(unit)
        return UnitOfTemperature.CELSIUS

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"source_entity": self._source_entity_id}


class AquariumDeltaSensor(AquariumBaseSensor):
    """Difference between aquarium water and source water."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = True

    def __init__(
        self,
        entry: AquariumManagerConfigEntry,
        *,
        key: str,
        name: str,
        tank_key: str,
        source_key: str,
        unit: str,
        icon: str,
    ) -> None:
        super().__init__(entry, key)
        self._attr_name = name
        self._tank_key = tank_key
        self._source_key = source_key
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

    @property
    def native_value(self) -> float | None:
        tank_value = self._entry.runtime_data.readings.get(self._tank_key)
        source_value = self._entry.runtime_data.readings.get(self._source_key)
        if tank_value is None or source_value is None:
            return None
        return round(tank_value - source_value, 2)


class AquariumWaterStatusSensor(AquariumBaseSensor):
    """Simple safety-oriented nitrogen-cycle status."""

    _attr_name = "Water status"
    _attr_icon = "mdi:water-check"
    _attr_should_poll = True

    def __init__(self, entry: AquariumManagerConfigEntry) -> None:
        super().__init__(entry, "water_status")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_STOCKING_UPDATED}_{self._entry.entry_id}",
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> str:
        readings = self._entry.runtime_data.readings
        ammonia = readings.get("ammonia")
        nitrite = readings.get("nitrite")
        nitrate = readings.get("nitrate")

        if ammonia is None or nitrite is None or nitrate is None:
            return "incomplete"
        if ammonia > 0.25 or nitrite > 0.25:
            return "danger"
        if ammonia > 0 or nitrite > 0 or nitrate >= 40:
            return "attention"
        return "clear"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        readings = self._entry.runtime_data.readings
        ammonia = readings.get("ammonia")
        nitrite = readings.get("nitrite")
        nitrate = readings.get("nitrate")
        reasons: list[str] = []

        if ammonia is None:
            reasons.append("Enter an ammonia reading")
        elif ammonia > 0:
            reasons.append(f"Ammonia is {ammonia} mg/L")
        if nitrite is None:
            reasons.append("Enter a nitrite reading")
        elif nitrite > 0:
            reasons.append(f"Nitrite is {nitrite} mg/L")
        if nitrate is None:
            reasons.append("Enter a nitrate reading")
        elif nitrate >= 40:
            reasons.append(f"Nitrate is {nitrate} mg/L")

        return {
            "reasons": reasons,
            "scope": "Water status checks ammonia, nitrite and nitrate only.",
            "last_submitted": self._entry.runtime_data.last_reading_submitted,
        }
