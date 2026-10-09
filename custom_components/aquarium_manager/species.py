"""GitHub-capable species library for Aquarium Manager."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import time
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientTimeout

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import (
    LIBRARY_MAX_BYTES,
    LIBRARY_MAX_SPECIES,
    LIBRARY_SCHEMA_VERSION,
    LIBRARY_STORAGE_KEY_PREFIX,
    LIBRARY_STORAGE_VERSION,
    PROFILE_FIELDS,
)

_LOGGER = logging.getLogger(__name__)

BUNDLED_LIBRARY_PATH = Path(__file__).with_name("fish_library.json")


class SpeciesLibraryError(ValueError):
    """Raised when a species library document is invalid."""


def _normalise(value: str) -> str:
    """Normalise a fish name for matching."""
    return " ".join(value.casefold().replace("-", " ").split())


def is_http_url(value: str) -> bool:
    """Return True when a string is an HTTP(S) URL."""
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeciesLibraryError(f"{field_name} must be a number or null")
    return float(value)


def _range_value(value: Any, field_name: str) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict):
        raise SpeciesLibraryError(f"{field_name} must be an object or null")
    minimum = _optional_number(value.get("min"), f"{field_name}.min")
    maximum = _optional_number(value.get("max"), f"{field_name}.max")
    if minimum is None or maximum is None:
        raise SpeciesLibraryError(f"{field_name} requires both min and max")
    if minimum > maximum:
        raise SpeciesLibraryError(f"{field_name}.min cannot exceed max")
    return minimum, maximum


def _string_list(value: Any, field_name: str, *, max_items: int = 50) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > max_items:
        raise SpeciesLibraryError(f"{field_name} must be a list with at most {max_items} items")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SpeciesLibraryError(f"{field_name} entries must be non-empty strings")
        result.append(item.strip())
    return result


def _required_string(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SpeciesLibraryError(f"{context}.{key} is required")
    return value.strip()


def _flatten_species(record: dict[str, Any], index: int) -> dict[str, Any]:
    """Validate one external species entry and convert it to the internal profile."""
    context = f"species[{index}]"
    profile_id = _required_string(record, "id", context)
    common_name = _required_string(record, "common_name", context)
    scientific_name = record.get("scientific_name", "")
    if scientific_name is None:
        scientific_name = ""
    if not isinstance(scientific_name, str):
        raise SpeciesLibraryError(f"{context}.scientific_name must be a string")

    temp_min, temp_max = _range_value(record.get("temperature"), f"{context}.temperature")
    ph_min, ph_max = _range_value(record.get("ph"), f"{context}.ph")
    gh_min, gh_max = _range_value(record.get("gh"), f"{context}.gh")
    kh_min, kh_max = _range_value(record.get("kh"), f"{context}.kh")

    minimum_group = record.get("minimum_group")
    if minimum_group is not None:
        if isinstance(minimum_group, bool) or not isinstance(minimum_group, int) or minimum_group < 1:
            raise SpeciesLibraryError(f"{context}.minimum_group must be a positive integer or null")

    minimum_tank = record.get("minimum_tank")
    if minimum_tank is None:
        minimum_tank = {}
    if not isinstance(minimum_tank, dict):
        raise SpeciesLibraryError(f"{context}.minimum_tank must be an object or null")

    source = record.get("source") or {}
    if not isinstance(source, dict):
        raise SpeciesLibraryError(f"{context}.source must be an object or null")

    # Newer importer versions store multiple source references in a mapping.
    # Prefer SeriouslyFish for aquarium-care provenance, then FishBase, while
    # remaining compatible with the original single "source" object.
    if not source:
        sources = record.get("sources") or {}
        if isinstance(sources, dict):
            for key, label in (("seriouslyfish", "Seriously Fish"), ("fishbase", "FishBase")):
                candidate = sources.get(key)
                if isinstance(candidate, dict) and candidate.get("url"):
                    source = {
                        "label": label,
                        "url": candidate.get("url", ""),
                    }
                    break
        elif isinstance(sources, list):
            for candidate in sources:
                if isinstance(candidate, dict) and candidate.get("url"):
                    source = candidate
                    break

    source_label = source.get("label", "") or ""
    source_url = source.get("url", "") or ""
    if not isinstance(source_label, str) or not isinstance(source_url, str):
        raise SpeciesLibraryError(f"{context}.source label/url must be strings")

    profile = {
        "profile_id": profile_id,
        "name": common_name,
        "scientific_name": scientific_name.strip(),
        "aliases": _string_list(record.get("aliases"), f"{context}.aliases"),
        "adult_size_cm": _optional_number(record.get("adult_size_cm"), f"{context}.adult_size_cm"),
        "temperature_min": temp_min,
        "temperature_max": temp_max,
        "ph_min": ph_min,
        "ph_max": ph_max,
        "gh_min": gh_min,
        "gh_max": gh_max,
        "kh_min": kh_min,
        "kh_max": kh_max,
        "min_group_size": minimum_group,
        "min_tank_length_cm": _optional_number(minimum_tank.get("length_cm"), f"{context}.minimum_tank.length_cm"),
        "min_tank_width_cm": _optional_number(minimum_tank.get("width_cm"), f"{context}.minimum_tank.width_cm"),
        "min_tank_height_cm": _optional_number(minimum_tank.get("height_cm"), f"{context}.minimum_tank.height_cm"),
        "min_tank_volume_l": _optional_number(record.get("minimum_tank_volume_l"), f"{context}.minimum_tank_volume_l"),
        "social_type": str(record.get("social_type") or "").strip(),
        "temperament": str(record.get("temperament") or "").strip(),
        "swimming_zone": str(record.get("swimming_zone") or "").strip(),
        "compatibility": str(record.get("compatibility") or "").strip(),
        "difficulty": str(record.get("difficulty") or "").strip(),
        "diet_type": str(record.get("diet_type") or "").strip(),
        "environment_type": str(record.get("environment_type") or "").strip(),
        "ecology": str(record.get("ecology") or "").strip(),
        "climate": str(record.get("climate") or "").strip(),
        "distribution": str(record.get("distribution") or "").strip(),
        "cautions": _string_list(record.get("cautions"), f"{context}.cautions", max_items=20),
        "source_label": source_label.strip(),
        "source_url": source_url.strip(),
    }
    return profile


def validate_library_document(document: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate a full external library document."""
    if not isinstance(document, dict):
        raise SpeciesLibraryError("Library root must be an object")

    if document.get("schema_version") != LIBRARY_SCHEMA_VERSION:
        raise SpeciesLibraryError(
            f"schema_version must be {LIBRARY_SCHEMA_VERSION}"
        )

    species = document.get("species")
    if not isinstance(species, list):
        raise SpeciesLibraryError("species must be a list")
    if len(species) > LIBRARY_MAX_SPECIES:
        raise SpeciesLibraryError(
            f"species contains more than {LIBRARY_MAX_SPECIES} entries"
        )

    profiles: dict[str, dict[str, Any]] = {}
    scientific_names: dict[str, str] = {}

    for index, record in enumerate(species):
        if not isinstance(record, dict):
            raise SpeciesLibraryError(f"species[{index}] must be an object")
        profile = _flatten_species(record, index)
        profile_id = profile["profile_id"]
        if profile_id in profiles:
            raise SpeciesLibraryError(f"Duplicate species id: {profile_id}")

        # Scientific names identify taxa and must stay unique. Common names and
        # aliases are much less controlled and may legitimately overlap.
        scientific_name = profile["scientific_name"]
        if scientific_name:
            normalised_scientific = _normalise(scientific_name)
            existing = scientific_names.get(normalised_scientific)
            if existing is not None and existing != profile_id:
                raise SpeciesLibraryError(
                    f"Duplicate scientific name in library: {scientific_name}"
                )
            scientific_names[normalised_scientific] = profile_id

        # Remove duplicates within a single profile, e.g. common name
        # "Bronze Cory" plus alias "bronze cory". This is harmless and should
        # not prevent the whole remote library from loading.
        local_names = {
            _normalise(value)
            for value in (profile["name"], profile["scientific_name"])
            if value
        }
        cleaned_aliases: list[str] = []
        for alias in profile["aliases"]:
            normalised_alias = _normalise(alias)
            if normalised_alias in local_names:
                continue
            local_names.add(normalised_alias)
            cleaned_aliases.append(alias)
        profile["aliases"] = cleaned_aliases

        profiles[profile_id] = profile

    metadata = {
        "schema_version": document["schema_version"],
        "library_name": str(document.get("library_name") or "Aquarium fish library"),
        "updated": str(document.get("updated") or ""),
    }
    return metadata, profiles


class SpeciesLibraryManager:
    """Load bundled, cached, and optional remote fish-library data."""

    def __init__(self, hass: HomeAssistant, entry_id: str, url: str = "") -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.url = url.strip()
        self.store: Store[dict[str, Any]] = Store(
            hass,
            LIBRARY_STORAGE_VERSION,
            f"{LIBRARY_STORAGE_KEY_PREFIX}.{entry_id}",
        )

        self.profiles: dict[str, dict[str, Any]] = {}
        self.bundled_profiles: dict[str, dict[str, Any]] = {}
        self.metadata: dict[str, Any] = {}
        self._alias_lookup: dict[str, str] = {}
        self._scientific_lookup: dict[str, str] = {}
        self._ambiguous_names: list[str] = []

        self.source = "bundled"
        self.last_refresh: str | None = None
        self.last_error: str | None = None
        self.content_sha256: str | None = None
        self.remote_etag: str | None = None
        self.remote_last_modified: str | None = None

    def _read_bundled_sync(self) -> dict[str, Any]:
        return json.loads(BUNDLED_LIBRARY_PATH.read_text(encoding="utf-8"))

    def _activate(self, document: dict[str, Any], source: str) -> None:
        metadata, profiles = validate_library_document(document)
        self.metadata = metadata
        self.profiles = profiles
        self.source = source
        self._alias_lookup = {}
        self._scientific_lookup = {}
        self._ambiguous_names = []

        # Scientific names are unique by validation and take precedence over
        # trade/common aliases during matching.
        for profile_id, profile in profiles.items():
            scientific_name = profile.get("scientific_name")
            if scientific_name:
                self._scientific_lookup[_normalise(scientific_name)] = profile_id

        # Common names and aliases can overlap across species. Only terms that
        # resolve to one profile are put into the automatic lookup map.
        candidates: dict[str, set[str]] = {}
        display_names: dict[str, str] = {}

        for profile_id, profile in profiles.items():
            for name in [profile["name"], *profile.get("aliases", [])]:
                if not name:
                    continue
                normalised = _normalise(name)
                candidates.setdefault(normalised, set()).add(profile_id)
                display_names.setdefault(normalised, name)

        for normalised, profile_ids in candidates.items():
            if normalised in self._scientific_lookup:
                # A real scientific name always wins over a colliding alias.
                continue
            if len(profile_ids) == 1:
                self._alias_lookup[normalised] = next(iter(profile_ids))
            else:
                self._ambiguous_names.append(display_names.get(normalised, normalised))

        self._ambiguous_names.sort(key=str.casefold)

        if self._ambiguous_names:
            _LOGGER.warning(
                "Fish library contains %d ambiguous common name/alias value(s): %s",
                len(self._ambiguous_names),
                ", ".join(self._ambiguous_names[:20]),
            )

    async def async_load(self) -> None:
        """Load bundled library, then cache, then try the configured remote URL."""
        bundled = await self.hass.async_add_executor_job(self._read_bundled_sync)
        _, self.bundled_profiles = validate_library_document(bundled)
        self._activate(bundled, "bundled")

        cached = await self.store.async_load() or {}
        cached_document = cached.get("document")
        if (
            self.url
            and cached.get("url") == self.url
            and isinstance(cached_document, dict)
        ):
            try:
                self._activate(cached_document, "cache")
                self.last_refresh = cached.get("last_refresh")
                self.content_sha256 = cached.get("content_sha256")
                self.remote_etag = cached.get("remote_etag")
                self.remote_last_modified = cached.get("remote_last_modified")
            except SpeciesLibraryError as err:
                _LOGGER.warning("Ignoring invalid cached fish library: %s", err)

        if self.url:
            await self.async_refresh(raise_on_error=False)

    async def async_refresh(self, *, raise_on_error: bool = False) -> bool:
        """Refresh from the configured URL, leaving the active library intact on failure."""
        if not self.url:
            self.last_error = None
            return True

        try:
            if not is_http_url(self.url):
                raise SpeciesLibraryError("Fish library URL must use http:// or https://")

            session = async_get_clientsession(self.hass)
            timeout = ClientTimeout(total=20)

            # GitHub raw/CDN responses can be cached. A unique query parameter
            # plus no-cache headers makes a manual/scheduled refresh fetch the
            # current repository content rather than a stale intermediary copy.
            cache_buster = str(time.time_ns())
            async with session.get(
                self.url,
                timeout=timeout,
                params={"aquarium_manager_refresh": cache_buster},
                headers={
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            ) as response:
                if response.status != 200:
                    raise SpeciesLibraryError(
                        f"Fish library returned HTTP {response.status}"
                    )
                raw = await response.read()
                remote_etag = response.headers.get("ETag")
                remote_last_modified = response.headers.get("Last-Modified")

            if len(raw) > LIBRARY_MAX_BYTES:
                raise SpeciesLibraryError(
                    f"Fish library is larger than {LIBRARY_MAX_BYTES // 1024 // 1024} MB"
                )

            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as err:
                raise SpeciesLibraryError(f"Fish library is not valid UTF-8 JSON: {err}") from err

            # Fully validate before replacing the currently active library.
            validate_library_document(document)
            self._activate(document, "remote")
            self.last_refresh = datetime.now(UTC).isoformat()
            self.last_error = None
            self.content_sha256 = hashlib.sha256(raw).hexdigest()
            self.remote_etag = remote_etag
            self.remote_last_modified = remote_last_modified
            await self.store.async_save(
                {
                    "url": self.url,
                    "document": document,
                    "last_refresh": self.last_refresh,
                    "content_sha256": self.content_sha256,
                    "remote_etag": self.remote_etag,
                    "remote_last_modified": self.remote_last_modified,
                }
            )
            return True

        except (ClientError, TimeoutError, SpeciesLibraryError) as err:
            self.last_error = str(err)
            _LOGGER.warning("Could not refresh fish library from %s: %s", self.url, err)
            if raise_on_error:
                raise HomeAssistantError(
                    f"Could not refresh fish library: {err}"
                ) from err
            return False

    async def async_apply_document(self, document: dict[str, Any]) -> None:
        """Activate a validated document immediately after a successful GitHub write."""
        validate_library_document(document)
        raw = (json.dumps(document, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        self._activate(document, "remote")
        self.last_refresh = datetime.now(UTC).isoformat()
        self.last_error = None
        self.content_sha256 = hashlib.sha256(raw).hexdigest()
        self.remote_etag = None
        self.remote_last_modified = None
        await self.store.async_save({
            "url": self.url,
            "document": document,
            "last_refresh": self.last_refresh,
            "content_sha256": self.content_sha256,
            "remote_etag": None,
            "remote_last_modified": None,
        })

    def match(self, name: str) -> dict[str, Any] | None:
        """Return a deep copy of a matching active profile."""
        normalised = _normalise(name)

        profile_id = self._scientific_lookup.get(normalised)
        if profile_id is None:
            profile_id = self._alias_lookup.get(normalised)

        if profile_id is None:
            return None
        return deepcopy(self.profiles[profile_id])

    def by_id(self, profile_id: str | None) -> dict[str, Any] | None:
        """Return a deep copy by profile id."""
        if not profile_id or profile_id not in self.profiles:
            return None
        return deepcopy(self.profiles[profile_id])

    def _infer_existing_overrides(self, item: dict[str, Any]) -> set[str]:
        """Infer v0.3 manual edits by comparing with the old bundled profile."""
        explicit = item.get("profile_overrides")
        if isinstance(explicit, list):
            return {str(value) for value in explicit if value in PROFILE_FIELDS}

        profile_id = item.get("profile_id")
        baseline = self.bundled_profiles.get(str(profile_id)) if profile_id else None
        if baseline is None:
            return set()

        overrides: set[str] = set()
        for key in PROFILE_FIELDS:
            if key in item and item.get(key) != baseline.get(key):
                overrides.add(key)
        return overrides

    def enrich_stock_item(self, item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Attach/update a species profile while preserving per-fish overrides."""
        original = deepcopy(item)
        updated = dict(item)
        overrides = self._infer_existing_overrides(updated)

        profile = self.by_id(updated.get("profile_id"))
        if profile is None and updated.get("name"):
            profile = self.match(str(updated["name"]))

        if profile is not None:
            updated["profile_id"] = profile["profile_id"]
            updated["profile_overrides"] = sorted(overrides)

            for key in PROFILE_FIELDS:
                if key not in overrides:
                    updated[key] = deepcopy(profile.get(key))

            # These are library-owned metadata and care notes, not personal overrides.
            updated["cautions"] = deepcopy(profile.get("cautions", []))
            updated["source_label"] = profile.get("source_label", "")
            updated["source_url"] = profile.get("source_url", "")
        else:
            defaults: dict[str, Any] = {
                "profile_id": None,
                "profile_overrides": sorted(overrides),
                "scientific_name": updated.get("scientific_name", ""),
                "adult_size_cm": None,
                "temperature_min": None,
                "temperature_max": None,
                "ph_min": None,
                "ph_max": None,
                "gh_min": None,
                "gh_max": None,
                "kh_min": None,
                "kh_max": None,
                "min_group_size": None,
                "min_tank_length_cm": None,
                "min_tank_width_cm": None,
                "min_tank_height_cm": None,
                "min_tank_volume_l": None,
                "social_type": "",
                "temperament": "",
                "swimming_zone": "",
                "compatibility": "",
                "difficulty": "",
                "diet_type": "",
                "environment_type": "",
                "ecology": "",
                "climate": "",
                "distribution": "",
                "cautions": [],
                "source_label": "",
                "source_url": "",
            }
            for key, value in defaults.items():
                if key not in updated:
                    updated[key] = deepcopy(value)

        return updated, updated != original

    def library_summary(self) -> list[dict[str, Any]]:
        """Return lightweight library data for frontend species search."""
        return [
            {
                "profile_id": profile["profile_id"],
                "name": profile["name"],
                "scientific_name": profile["scientific_name"],
                "aliases": list(profile.get("aliases", [])),
            }
            for profile in sorted(
                self.profiles.values(), key=lambda value: value["name"].casefold()
            )
        ]

    @property
    def status(self) -> dict[str, Any]:
        """Return user-facing library metadata."""
        return {
            "species_count": len(self.profiles),
            "source": self.source,
            "library_name": self.metadata.get("library_name", "Aquarium fish library"),
            "schema_version": self.metadata.get("schema_version", LIBRARY_SCHEMA_VERSION),
            "updated": self.metadata.get("updated", ""),
            "url": self.url,
            "last_refresh": self.last_refresh,
            "last_error": self.last_error,
            "content_sha256": self.content_sha256,
            "remote_etag": self.remote_etag,
            "remote_last_modified": self.remote_last_modified,
            "ambiguous_name_count": len(self._ambiguous_names),
            "ambiguous_names": list(self._ambiguous_names[:50]),
        }
