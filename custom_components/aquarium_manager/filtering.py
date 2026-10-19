"""Filter data helpers for Aquarium Manager."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

FILTER_MEDIA_TYPES = {"mechanical", "biological", "chemical", "other"}

FLUVAL_307_DEFAULT: dict[str, Any] = {
    "id": "primary",
    "manufacturer": "Fluval",
    "model": "307",
    "filter_type": "External canister",
    "rated_tank_min_l": 90.0,
    "rated_tank_max_l": 330.0,
    "pump_output_lph": 1150.0,
    "rated_circulation_lph": 780.0,
    "media_basket_volume_l": 3.1,
    "canister_volume_l": 7.3,
    "measured_flow_lph": None,
    "maintenance_interval_days": 30,
    "last_cleaned": None,
    "notes": "",
    "stages": [
        {
            "id": "prefilter",
            "name": "Vertical pre-filter",
            "media": [
                {
                    "name": "Coarse foam / Bio-Foam Max",
                    "type": "mechanical",
                    "amount": "",
                    "notes": "",
                    "last_serviced": None,
                    "replace_interval_days": None,
                },
                {
                    "name": "Fine foam / Bio-Foam",
                    "type": "mechanical",
                    "amount": "",
                    "notes": "",
                    "last_serviced": None,
                    "replace_interval_days": None,
                },
            ],
        },
        {
            "id": "basket_1",
            "name": "Bottom basket",
            "media": [
                {
                    "name": "Fine mechanical foam / polishing media",
                    "type": "mechanical",
                    "amount": "",
                    "notes": "",
                    "last_serviced": None,
                    "replace_interval_days": None,
                }
            ],
        },
        {
            "id": "basket_2",
            "name": "Middle basket",
            "media": [
                {
                    "name": "Ceramic rings / BioMax",
                    "type": "biological",
                    "amount": "",
                    "notes": "",
                    "last_serviced": None,
                    "replace_interval_days": None,
                }
            ],
        },
        {
            "id": "basket_3",
            "name": "Top basket",
            "media": [
                {
                    "name": "Ceramic rings / biological media",
                    "type": "biological",
                    "amount": "",
                    "notes": "",
                    "last_serviced": None,
                    "replace_interval_days": None,
                }
            ],
        },
    ],
}


def default_filter_data() -> dict[str, Any]:
    """Return an independent default Fluval 307 profile."""
    return deepcopy(FLUVAL_307_DEFAULT)


def _number(value: Any, default: float | None, *, minimum: float = 0.0, maximum: float = 100000.0) -> float | None:
    if value in (None, ""):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result < minimum or result > maximum:
        return default
    return result


def _int(value: Any, default: int | None, *, minimum: int = 1, maximum: int = 3650) -> int | None:
    if value in (None, ""):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < minimum or result > maximum:
        return default
    return result


def _text(value: Any, default: str = "", limit: int = 250) -> str:
    return str(value if value is not None else default).strip()[:limit]


def _iso_or_none(value: Any) -> str | None:
    raw = _text(value, "", 80)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def normalize_filter_data(raw: Any) -> dict[str, Any]:
    """Validate an editable filter record while preserving the Fluval 307 defaults."""
    base = default_filter_data()
    if not isinstance(raw, dict):
        return base

    for key, limit in (
        ("id", 80),
        ("manufacturer", 100),
        ("model", 100),
        ("filter_type", 100),
        ("notes", 1000),
    ):
        if key in raw:
            base[key] = _text(raw.get(key), str(base.get(key, "")), limit)

    for key in (
        "rated_tank_min_l",
        "rated_tank_max_l",
        "pump_output_lph",
        "rated_circulation_lph",
        "media_basket_volume_l",
        "canister_volume_l",
    ):
        if key in raw:
            base[key] = _number(raw.get(key), base.get(key), minimum=0.0)

    if "measured_flow_lph" in raw:
        base["measured_flow_lph"] = _number(raw.get("measured_flow_lph"), None, minimum=0.0)

    if "maintenance_interval_days" in raw:
        base["maintenance_interval_days"] = _int(
            raw.get("maintenance_interval_days"),
            int(base["maintenance_interval_days"]),
            minimum=1,
            maximum=3650,
        )

    if "last_cleaned" in raw:
        base["last_cleaned"] = _iso_or_none(raw.get("last_cleaned"))

    stages = raw.get("stages")
    if isinstance(stages, list):
        normalized_stages: list[dict[str, Any]] = []
        for index, stage in enumerate(stages[:12]):
            if not isinstance(stage, dict):
                continue
            stage_id = _text(stage.get("id"), f"stage_{index + 1}", 80) or f"stage_{index + 1}"
            stage_name = _text(stage.get("name"), f"Stage {index + 1}", 120) or f"Stage {index + 1}"
            media_items: list[dict[str, Any]] = []
            media = stage.get("media")
            if isinstance(media, list):
                for item in media[:20]:
                    if not isinstance(item, dict):
                        continue
                    name = _text(item.get("name"), "", 160)
                    if not name:
                        continue
                    media_type = _text(item.get("type"), "other", 40).casefold()
                    if media_type not in FILTER_MEDIA_TYPES:
                        media_type = "other"
                    media_items.append(
                        {
                            "name": name,
                            "type": media_type,
                            "amount": _text(item.get("amount"), "", 100),
                            "notes": _text(item.get("notes"), "", 500),
                            "last_serviced": _iso_or_none(item.get("last_serviced")),
                            "replace_interval_days": _int(
                                item.get("replace_interval_days"),
                                None,
                                minimum=1,
                                maximum=3650,
                            ),
                        }
                    )
            normalized_stages.append(
                {
                    "id": stage_id,
                    "name": stage_name,
                    "media": media_items,
                }
            )
        if normalized_stages:
            base["stages"] = normalized_stages

    return base


def filter_summary(filter_data: Any, tank_volume_l: float) -> dict[str, Any]:
    """Return practical filter metrics without treating filtration as a stocking licence."""
    data = normalize_filter_data(filter_data)
    measured = _number(data.get("measured_flow_lph"), None, minimum=0.0)
    rated = _number(data.get("rated_circulation_lph"), 0.0, minimum=0.0) or 0.0
    effective_flow = measured if measured and measured > 0 else rated
    flow_source = "measured" if measured and measured > 0 else "rated"

    turnover = effective_flow / tank_volume_l if effective_flow > 0 and tank_volume_l > 0 else None
    rated_min = _number(data.get("rated_tank_min_l"), None, minimum=0.0)
    rated_max = _number(data.get("rated_tank_max_l"), None, minimum=0.0)

    if rated_min is not None and tank_volume_l < rated_min:
        sizing_status = "below_manufacturer_range"
    elif rated_max is not None and tank_volume_l > rated_max:
        sizing_status = "above_manufacturer_range"
    elif rated_min is not None and rated_max is not None:
        sizing_status = "within_manufacturer_range"
    else:
        sizing_status = "unknown"

    media_counts = {key: 0 for key in FILTER_MEDIA_TYPES}
    media_total = 0
    for stage in data.get("stages", []):
        for item in stage.get("media", []):
            media_type = str(item.get("type") or "other").casefold()
            if media_type not in media_counts:
                media_type = "other"
            media_counts[media_type] += 1
            media_total += 1

    last_cleaned = _iso_or_none(data.get("last_cleaned"))
    interval = _int(data.get("maintenance_interval_days"), 30, minimum=1, maximum=3650) or 30
    next_maintenance = None
    days_to_maintenance = None
    maintenance_status = "not_recorded"
    if last_cleaned:
        cleaned_dt = datetime.fromisoformat(last_cleaned)
        due_dt = cleaned_dt + timedelta(days=interval)
        now = datetime.now(UTC)
        next_maintenance = due_dt.isoformat()
        days_to_maintenance = (due_dt.date() - now.date()).days
        maintenance_status = "overdue" if due_dt < now else "due_soon" if days_to_maintenance <= 7 else "ok"

    if turnover is None:
        flow_status = "unknown"
    elif turnover < 3.0:
        flow_status = "low"
    elif turnover <= 10.0:
        flow_status = "good"
    else:
        flow_status = "high"

    return {
        "manufacturer": data.get("manufacturer"),
        "model": data.get("model"),
        "filter_type": data.get("filter_type"),
        "effective_flow_lph": round(effective_flow, 1) if effective_flow else None,
        "flow_source": flow_source,
        "rated_circulation_lph": rated or None,
        "measured_flow_lph": measured,
        "turnover_per_hour": round(turnover, 2) if turnover is not None else None,
        "flow_status": flow_status,
        "sizing_status": sizing_status,
        "rated_tank_min_l": rated_min,
        "rated_tank_max_l": rated_max,
        "media_basket_volume_l": data.get("media_basket_volume_l"),
        "canister_volume_l": data.get("canister_volume_l"),
        "media_counts": media_counts,
        "media_total": media_total,
        "mechanical_media_present": media_counts["mechanical"] > 0,
        "biological_media_present": media_counts["biological"] > 0,
        "chemical_media_present": media_counts["chemical"] > 0,
        "maintenance_interval_days": interval,
        "last_cleaned": last_cleaned,
        "next_maintenance": next_maintenance,
        "days_to_maintenance": days_to_maintenance,
        "maintenance_status": maintenance_status,
    }
