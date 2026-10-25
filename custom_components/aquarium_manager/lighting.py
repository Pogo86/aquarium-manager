"""Aquarium lighting and CO2 scheduler."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_LIGHTING_UPDATED


LIGHTING_STORAGE_VERSION = 1
LIGHTING_STORAGE_KEY_PREFIX = "aquarium_manager.lighting"
MODE_ORDER = ("full", "half", "blue")
MODE_INDEX = {name: index for index, name in enumerate(MODE_ORDER)}
RESET_OFF_SECONDS = 4.0


@dataclass(slots=True)
class LightingSchedule:
    """Calculated schedule for one aquarium day."""

    start: datetime
    dim_start: datetime
    main_end: datetime
    blue_end: datetime
    co2_on: datetime
    co2_off: datetime

    def as_attributes(self) -> dict[str, str]:
        return {
            "co2_on": self.co2_on.isoformat(),
            "lights_on": self.start.isoformat(),
            "half_light_start": self.dim_start.isoformat(),
            "co2_off": self.co2_off.isoformat(),
            "blue_light_start": self.main_end.isoformat(),
            "lights_off": self.blue_end.isoformat(),
        }


class AquariumLightingController:
    """Manage two mode-cycling aquarium lights and one CO2 switch."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry_id: str,
        light_switches: list[str],
        co2_switch: str,
        enabled: bool,
        lights_on_time: str,
        light_duration_hours: float,
        half_light_duration_min: int,
        blue_light_duration_min: int,
        co2_lead_min: int,
        co2_off_before_end_min: int,
        mode_toggle_seconds: float,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.light_switches = [entity for entity in light_switches if entity]
        self.co2_switch = co2_switch
        self.enabled = enabled
        self.lights_on_time = lights_on_time
        self.light_duration_hours = float(light_duration_hours)
        self.half_light_duration_min = max(0, int(half_light_duration_min))
        self.blue_light_duration_min = max(0, int(blue_light_duration_min))
        self.co2_lead_min = max(0, int(co2_lead_min))
        self.co2_off_before_end_min = max(0, int(co2_off_before_end_min))
        self.mode_toggle_seconds = max(0.2, float(mode_toggle_seconds))

        self.status = "disabled" if not enabled else "off"
        self.current_mode: str | None = None
        self.reason = ""
        self.day_off_date: str | None = None
        self.last_action: str | None = None
        self.last_action_at: str | None = None
        self._store = Store(
            hass,
            LIGHTING_STORAGE_VERSION,
            f"{LIGHTING_STORAGE_KEY_PREFIX}.{entry_id}",
        )
        self._unsubs: list[Any] = []
        self._lock = asyncio.Lock()

    async def async_start(self) -> None:
        """Load state and start periodic scheduling."""
        saved = await self._store.async_load() or {}
        self.day_off_date = saved.get("day_off_date")
        self.current_mode = saved.get("current_mode")
        if self.current_mode not in MODE_ORDER:
            self.current_mode = None

        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_interval,
                timedelta(seconds=30),
            )
        )
        if self.light_switches or self.co2_switch:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass,
                    [*self.light_switches, *([self.co2_switch] if self.co2_switch else [])],
                    self._async_source_changed,
                )
            )
        await self.async_evaluate(force_sync=True)

    async def async_stop(self) -> None:
        """Stop the controller and fail safe by disabling CO2."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self.enabled and self.co2_switch:
            if self.co2_switch:
                await self._async_switch(self.co2_switch, False)

    async def _async_interval(self, _now: datetime) -> None:
        await self.async_evaluate()

    async def _async_source_changed(self, _event: Any) -> None:
        # Let a manual/offline switch change settle, then correct it on the next loop.
        await self.async_evaluate()

    def _parse_start_time(self) -> time:
        raw = str(self.lights_on_time or "12:00:00")
        parts = raw.split(":")
        hour = int(parts[0]) if parts else 12
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0
        return time(hour=hour, minute=minute, second=second)

    def _schedule_for(self, schedule_date: date) -> LightingSchedule:
        tz = dt_util.DEFAULT_TIME_ZONE
        start = datetime.combine(schedule_date, self._parse_start_time(), tzinfo=tz)
        main_end = start + timedelta(hours=self.light_duration_hours)
        dim_start = max(
            start,
            main_end - timedelta(minutes=self.half_light_duration_min),
        )
        blue_end = main_end + timedelta(minutes=self.blue_light_duration_min)
        co2_on = start - timedelta(minutes=self.co2_lead_min)
        co2_off = main_end - timedelta(minutes=self.co2_off_before_end_min)
        if co2_off < co2_on:
            co2_off = co2_on
        return LightingSchedule(
            start=start,
            dim_start=dim_start,
            main_end=main_end,
            blue_end=blue_end,
            co2_on=co2_on,
            co2_off=co2_off,
        )

    def _current_schedule(self, now: datetime) -> LightingSchedule | None:
        for offset in (-1, 0, 1):
            candidate = self._schedule_for((now + timedelta(days=offset)).date())
            if candidate.co2_on <= now < candidate.blue_end:
                return candidate
        return None

    def _next_schedule(self, now: datetime) -> LightingSchedule:
        today = self._schedule_for(now.date())
        if now < today.co2_on:
            return today
        return self._schedule_for((now + timedelta(days=1)).date())

    def _override_active(self, now: datetime) -> bool:
        return self.day_off_date == now.date().isoformat()

    async def async_set_day_off(self, enabled: bool) -> None:
        """Disable lights and CO2 for the rest of the local calendar day."""
        now = dt_util.now()
        self.day_off_date = now.date().isoformat() if enabled else None
        await self._async_save()
        await self.async_evaluate(force_sync=True)

    @property
    def is_day_off(self) -> bool:
        return self._override_active(dt_util.now())

    async def async_resync(self) -> None:
        """Force a clean mode reset and re-apply the current schedule."""
        self.current_mode = None
        if self.light_switches:
            await self._async_switch_many(self.light_switches, False)
            await asyncio.sleep(RESET_OFF_SECONDS)
        await self.async_evaluate(force_sync=True)

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "day_off_date": self.day_off_date,
                "current_mode": self.current_mode,
            }
        )

    def _unavailable_entities(self) -> list[str]:
        unavailable: list[str] = []
        for entity in [*self.light_switches, *([self.co2_switch] if self.co2_switch else [])]:
            if not entity:
                continue
            state = self.hass.states.get(entity)
            if state is None or state.state in {"unknown", "unavailable"}:
                unavailable.append(entity)
        return unavailable

    async def async_evaluate(self, *, force_sync: bool = False) -> None:
        """Calculate and apply the desired lighting/CO2 state."""
        async with self._lock:
            now = dt_util.now()

            if self.day_off_date and self.day_off_date < now.date().isoformat():
                self.day_off_date = None
                await self._async_save()

            if not self.enabled:
                # Fail safe when automation is disabled from the dashboard/config:
                # never leave scheduled CO2 running with no controller managing it.
                if self.light_switches:
                    await self._async_switch_many(self.light_switches, False)
                if self.co2_switch:
                    if self.co2_switch:
                    if self.co2_switch:
                await self._async_switch(self.co2_switch, False)
                self.current_mode = None
                self.status = "disabled"
                self.reason = "Lighting automation disabled; lights and CO2 are off"
                await self._async_save()
                self._notify()
                return

            if not self.light_switches:
                self.status = "not_configured"
                self.reason = "At least one light switch is required"
                self._notify()
                return

            unavailable = self._unavailable_entities()
            if unavailable:
                # Never continue scheduled CO2 dosing when equipment state cannot
                # be trusted. If the CO2 switch itself is still available, force it off.
                if self.co2_switch and self.co2_switch not in unavailable:
                    if self.co2_switch:
                    if self.co2_switch:
                await self._async_switch(self.co2_switch, False)
                self.status = "equipment_unavailable"
                self.reason = "Unavailable: " + ", ".join(unavailable)
                self._notify()
                return

            if self._override_active(now):
                await self._async_switch_many(self.light_switches, False)
                if self.co2_switch:
                    if self.co2_switch:
                await self._async_switch(self.co2_switch, False)
                self.current_mode = None
                self.status = "day_off"
                self.reason = "Lights off for today override is active"
                await self._async_save()
                self._notify()
                return

            schedule = self._current_schedule(now)
            if schedule is None:
                await self._async_switch_many(self.light_switches, False)
                if self.co2_switch:
                    if self.co2_switch:
                await self._async_switch(self.co2_switch, False)
                self.current_mode = None
                self.status = "off"
                self.reason = "Outside the scheduled lighting/CO2 window"
                await self._async_save()
                self._notify()
                return

            co2_should_be_on = schedule.co2_on <= now < schedule.co2_off
            if self.co2_switch:
                await self._async_switch(self.co2_switch, co2_should_be_on)

            if now < schedule.start:
                await self._async_switch_many(self.light_switches, False)
                self.current_mode = None
                self.status = "co2_precharge"
                self.reason = "CO2 pre-charge before lights on"
            elif now < schedule.dim_start:
                await self._async_ensure_mode("full", force_sync=force_sync)
                self.status = "full"
                self.reason = "Main full-light period"
            elif now < schedule.main_end:
                await self._async_ensure_mode("half", force_sync=force_sync)
                self.status = "half"
                self.reason = "End-of-day reduced light"
            elif now < schedule.blue_end:
                if self.co2_switch:
                    if self.co2_switch:
                await self._async_switch(self.co2_switch, False)
                await self._async_ensure_mode("blue", force_sync=force_sync)
                self.status = "blue"
                self.reason = "Blue-light viewing period"
            else:
                await self._async_switch_many(self.light_switches, False)
                if self.co2_switch:
                    if self.co2_switch:
                await self._async_switch(self.co2_switch, False)
                self.current_mode = None
                self.status = "off"
                self.reason = "Schedule complete"

            await self._async_save()
            self._notify()

    def _lights_all_on(self) -> bool:
        if not self.light_switches:
            return False
        return all(
            (state := self.hass.states.get(entity)) is not None
            and state.state == STATE_ON
            for entity in self.light_switches
        )

    async def _async_ensure_mode(self, target: str, *, force_sync: bool = False) -> None:
        if target not in MODE_ORDER:
            return

        if force_sync or not self._lights_all_on():
            # A long OFF period is assumed to reset these lamps to their first
            # (full light) mode. This is the only reliable synchronisation point
            # when the lamps expose no mode feedback to Home Assistant.
            await self._async_switch_many(self.light_switches, False)
            await asyncio.sleep(RESET_OFF_SECONDS)
            await self._async_switch_many(self.light_switches, True)
            await asyncio.sleep(self.mode_toggle_seconds)
            self.current_mode = "full"
        elif self.current_mode not in MODE_ORDER:
            # HA may have restarted while the lights were already on. Resynchronise
            # from a known full-light cold start before applying half/blue.
            await self._async_switch_many(self.light_switches, False)
            await asyncio.sleep(RESET_OFF_SECONDS)
            await self._async_switch_many(self.light_switches, True)
            await asyncio.sleep(self.mode_toggle_seconds)
            self.current_mode = "full"

        cycles = (MODE_INDEX[target] - MODE_INDEX[self.current_mode]) % len(MODE_ORDER)
        for _ in range(cycles):
            await self._async_switch_many(self.light_switches, False)
            await asyncio.sleep(self.mode_toggle_seconds)
            await self._async_switch_many(self.light_switches, True)
            await asyncio.sleep(self.mode_toggle_seconds)
            current_index = (MODE_INDEX[self.current_mode] + 1) % len(MODE_ORDER)
            self.current_mode = MODE_ORDER[current_index]

        if cycles:
            self._record_action(f"Set both lights to {target} mode")

    async def _async_switch_many(self, entity_ids: list[str], on: bool) -> None:
        if not entity_ids:
            return
        target_state = STATE_ON if on else "off"
        if all(
            (state := self.hass.states.get(entity)) is not None
            and state.state == target_state
            for entity in entity_ids
        ):
            return
        await self.hass.services.async_call(
            "switch",
            "turn_on" if on else "turn_off",
            {"entity_id": entity_ids},
            blocking=True,
        )
        self._record_action(f"Turned {'on' if on else 'off'} aquarium lights")

    async def _async_switch(self, entity_id: str, on: bool) -> None:
        if not entity_id:
            return
        state = self.hass.states.get(entity_id)
        if state is not None and state.state == (STATE_ON if on else "off"):
            return
        await self.hass.services.async_call(
            "switch",
            "turn_on" if on else "turn_off",
            {"entity_id": entity_id},
            blocking=True,
        )
        self._record_action(
            f"Turned {'on' if on else 'off'} "
            f"{'CO2' if entity_id == self.co2_switch else entity_id}"
        )

    def _record_action(self, text: str) -> None:
        self.last_action = text
        self.last_action_at = dt_util.utcnow().isoformat()

    def _notify(self) -> None:
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_LIGHTING_UPDATED}_{self.entry_id}",
        )

    @property
    def status_attributes(self) -> dict[str, Any]:
        now = dt_util.now()
        schedule = self._current_schedule(now) or self._next_schedule(now)
        light_states = {
            entity: self.hass.states.get(entity).state
            if self.hass.states.get(entity) is not None
            else "missing"
            for entity in self.light_switches
        }
        co2_state = (
            self.hass.states.get(self.co2_switch).state
            if self.co2_switch and self.hass.states.get(self.co2_switch) is not None
            else "missing"
        )
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "current_mode": self.current_mode,
            "lights_off_today": self.is_day_off,
            "lights_off_today_date": self.day_off_date,
            "light_switches": list(self.light_switches),
            "light_states": light_states,
            "co2_switch": self.co2_switch,
            "co2_state": co2_state,
            "lights_on_time": self.lights_on_time,
            "light_duration_hours": self.light_duration_hours,
            "half_light_duration_min": self.half_light_duration_min,
            "blue_light_duration_min": self.blue_light_duration_min,
            "co2_lead_min": self.co2_lead_min,
            "co2_off_before_end_min": self.co2_off_before_end_min,
            "mode_toggle_seconds": self.mode_toggle_seconds,
            "last_action": self.last_action,
            "last_action_at": self.last_action_at,
            **schedule.as_attributes(),
        }
