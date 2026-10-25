"""Regression tests for shared GitHub library configuration."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aquarium_manager import (
    async_save_shared_github_settings,
    async_shared_github_settings,
    SHARED_GITHUB_STORE_KEY,
)


@pytest.fixture
def fake_hass():
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    hass.config_entries.async_reload = AsyncMock()
    hass.async_create_task.side_effect = lambda coroutine: coroutine.close()
    return hass


@pytest.mark.asyncio
async def test_migrates_legacy_options(fake_hass):
    first = MagicMock()
    first.options = {"species_library_url": "https://example.org/fish.json", "github_token": "secret"}
    fake_hass.config_entries.async_entries.return_value = [first]
    with patch("custom_components.aquarium_manager.Store") as store_class:
        store = store_class.return_value
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        url, token = await async_shared_github_settings(fake_hass)
        assert url == "https://example.org/fish.json"
        assert token == "secret"
        store.async_save.assert_awaited_once_with({"url": url, "token": token})
        assert store_class.call_args.args[2] == SHARED_GITHUB_STORE_KEY


@pytest.mark.asyncio
async def test_saved_shared_values_override_legacy(fake_hass):
    with patch("custom_components.aquarium_manager.Store") as store_class:
        store_class.return_value.async_load = AsyncMock(
            return_value={"url": "https://example.org/shared.json", "token": "shared"}
        )
        assert await async_shared_github_settings(fake_hass) == (
            "https://example.org/shared.json", "shared"
        )


@pytest.mark.asyncio
async def test_unchanged_settings_do_not_reload(fake_hass):
    with patch("custom_components.aquarium_manager.Store") as store_class:
        store_class.return_value.async_load = AsyncMock(
            return_value={"url": "https://example.org/fish.json", "token": "secret"}
        )
        store_class.return_value.async_save = AsyncMock()
        await async_save_shared_github_settings(
            fake_hass, "https://example.org/fish.json", "secret"
        )
        store_class.return_value.async_save.assert_not_awaited()
        fake_hass.config_entries.async_reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_settings_reload_other_tanks(fake_hass):
    first, second = MagicMock(), MagicMock()
    first.entry_id, second.entry_id = "tank_a", "tank_b"
    fake_hass.config_entries.async_entries.return_value = [first, second]
    scheduled = []
    fake_hass.async_create_task.side_effect = lambda coro: scheduled.append(coro)
    with patch("custom_components.aquarium_manager.Store") as store_class:
        store_class.return_value.async_load = AsyncMock(
            return_value={"url": "old", "token": "old"}
        )
        store_class.return_value.async_save = AsyncMock()
        await async_save_shared_github_settings(
            fake_hass, "new", "new-token", exclude_entry_id="tank_a"
        )
        store_class.return_value.async_save.assert_awaited_once_with(
            {"url": "new", "token": "new-token"}
        )
        assert len(scheduled) == 1
        await scheduled[0]
        fake_hass.config_entries.async_reload.assert_awaited_once_with("tank_b")
