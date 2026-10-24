"""Shared Aquarium Manager settings used by every aquarium entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import CONF_GITHUB_TOKEN, CONF_SPECIES_LIBRARY_URL, DOMAIN


SHARED_STORAGE_VERSION = 1
SHARED_STORAGE_KEY = "aquarium_manager.shared_settings"
SHARED_DATA_KEY = "shared_settings"


@dataclass(slots=True)
class SharedAquariumSettings:
    """Persist settings that belong to Aquarium Manager as a whole."""

    hass: HomeAssistant
    species_library_url: str = ""
    github_token: str = ""

    def __post_init__(self) -> None:
        self._store = Store[dict[str, Any]](
            self.hass,
            SHARED_STORAGE_VERSION,
            SHARED_STORAGE_KEY,
        )

    async def async_load_and_migrate(self, entries: list[ConfigEntry]) -> None:
        """Load global settings and migrate legacy per-entry values once."""
        saved = await self._store.async_load() or {}
        self.species_library_url = str(
            saved.get(CONF_SPECIES_LIBRARY_URL, "") or ""
        ).strip()
        self.github_token = str(saved.get(CONF_GITHUB_TOKEN, "") or "").strip()

        changed = False
        if not self.species_library_url:
            for entry in entries:
                candidate = str(
                    entry.options.get(CONF_SPECIES_LIBRARY_URL, "") or ""
                ).strip()
                if candidate:
                    self.species_library_url = candidate
                    changed = True
                    break

        if not self.github_token:
            for entry in entries:
                candidate = str(
                    entry.options.get(CONF_GITHUB_TOKEN, "") or ""
                ).strip()
                if candidate:
                    self.github_token = candidate
                    changed = True
                    break

        if changed:
            await self.async_save()

    async def async_save(self) -> None:
        """Persist shared library credentials/settings."""
        await self._store.async_save(
            {
                CONF_SPECIES_LIBRARY_URL: self.species_library_url,
                CONF_GITHUB_TOKEN: self.github_token,
            }
        )

    async def async_update(
        self,
        *,
        species_library_url: str,
        github_token: str,
    ) -> None:
        """Update shared settings for every aquarium."""
        self.species_library_url = str(species_library_url or "").strip()
        self.github_token = str(github_token or "").strip()
        await self.async_save()


async def async_get_shared_settings(hass: HomeAssistant) -> SharedAquariumSettings:
    """Return the loaded shared settings manager."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    current = domain_data.get(SHARED_DATA_KEY)
    if isinstance(current, SharedAquariumSettings):
        return current

    manager = SharedAquariumSettings(hass)
    await manager.async_load_and_migrate(hass.config_entries.async_entries(DOMAIN))
    domain_data[SHARED_DATA_KEY] = manager
    return manager
