"""Fail-safe backup-heater controller for Aquarium Manager."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import (
    SIGNAL_HEATER_UPDATED,
)


@dataclass(frozen=True)
class HeaterDecision:
    """Pure controller decision used by the runtime and tests."""

    status: str
    action: str | None = None
    lockout: bool = False
    reason: str = ""


def decide_backup_heater(
    *,
    enabled: bool,
    temperature_c: float,
    heater_on: bool,
    lockout: bool,
    low_elapsed_seconds: float | None,
    on_elapsed_seconds: float | None,
    on_temp_c: float,
    off_temp_c: float,
    low_delay_seconds: float,
    max_runtime_seconds: float,
) -> HeaterDecision:
    """Return the safe next action for a valid temperature/switch state."""
    if not enabled:
        return HeaterDecision("disabled", reason="Automation disabled")

    if temperature_c >= off_temp_c:
        return HeaterDecision(
            "standby",
            action="off" if heater_on else None,
            lockout=False,
            reason=f"Temperature recovered to {temperature_c:.1f} °C",
        )

    if lockout:
        return HeaterDecision(
            "lockout",
            action="off" if heater_on else None,
            lockout=True,
            reason="Maximum runtime lockout; waits for temperature recovery",
        )

    if heater_on:
        if (
            on_elapsed_seconds is not None
            and on_elapsed_seconds >= max_runtime_seconds
        ):
            return HeaterDecision(
                "lockout",
                action="off",
                lockout=True,
                reason="Maximum continuous runtime reached",
            )
        return HeaterDecision(
            "heating",
            lockout=False,
            reason=f"Heating until {off_temp_c:.1f} °C",
        )

    if temperature_c < on_temp_c:
        elapsed = low_elapsed_seconds or 0.0
        if elapsed >= low_delay_seconds:
            return HeaterDecision(
                "heating",
                action="on",
                lockout=False,
                reason=(
                    f"Temperature remained below {on_temp_c:.1f} °C "
                    f"for {low_delay_seconds / 60:.0f} min"
                ),
            )
        remaining = max(0.0, low_delay_seconds - elapsed)
        return HeaterDecision(
            "waiting",
            lockout=False,
            reason=f"Low-temperature delay: {remaining / 60:.1f} min remaining",
        )

    return HeaterDecision(
        "standby",
        lockout=False,
        reason=(
            f"Temperature is between ON {on_temp_c:.1f} °C and "
            f"OFF {off_temp_c:.1f} °C thresholds"
        ),
    )


class BackupHeaterController:
    """Manage the configured backup heater from the configured temperature probe."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry_id: str,
        temperature_entity: str,
        heater_switch: str,
        enabled: bool,
        on_temp_c: float,
        off_temp_c: float,
        low_delay_min: int,
        max_runtime_min: int,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.temperature_entity = temperature_entity
        self.heater_switch = heater_switch
        self.enabled = enabled
        self.on_temp_c = float(on_temp_c)
        self.off_temp_c = float(off_temp_c)
        self.low_delay = timedelta(minutes=int(low_delay_min))
        self.max_runtime = timedelta(minutes=int(max_runtime_min))

        self.status = "starting"
        self.current_temperature_c: float | None = None
        self.below_since: datetime | None = None
        self.heater_on_since: datetime | None = None
        self.lockout = False
        self.last_action: str | None = None
        self.last_reason = "Starting controller"
        self.last_evaluated: str | None = None
        self._unsubs: list[Any] = []

    @property
    def status_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the status sensor/card."""
        now = datetime.now(UTC)
        runtime_min: float | None = None
        low_for_min: float | None = None
        if self.heater_on_since is not None:
            runtime_min = round((now - self.heater_on_since).total_seconds() / 60, 1)
        if self.below_since is not None:
            low_for_min = round((now - self.below_since).total_seconds() / 60, 1)
        return {
            "enabled": self.enabled,
            "temperature_entity": self.temperature_entity,
            "heater_switch": self.heater_switch,
            "current_temperature_c": self.current_temperature_c,
            "on_below_c": self.on_temp_c,
            "off_at_c": self.off_temp_c,
            "low_delay_min": int(self.low_delay.total_seconds() / 60),
            "max_runtime_min": int(self.max_runtime.total_seconds() / 60),
            "low_for_min": low_for_min,
            "current_runtime_min": runtime_min,
            "lockout": self.lockout,
            "last_action": self.last_action,
            "reason": self.last_reason,
            "last_evaluated": self.last_evaluated,
        }

    async def async_start(self) -> None:
        """Start state/timer listeners and immediately evaluate."""
        self._unsubs.append(
            async_track_state_change_event(
                self.hass,
                [self.temperature_entity, self.heater_switch],
                self._async_state_changed,
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_interval,
                timedelta(seconds=30),
            )
        )
        await self.async_evaluate("startup")

    async def async_stop(self) -> None:
        """Stop listeners and fail safe by turning off an automation-managed heater."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self.enabled and self._heater_is_on():
            try:
                await self._async_turn_off("Controller stopped/reloaded")
            except Exception:  # Home Assistant will log the underlying service error.
                pass

    @callback
    def _async_state_changed(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_evaluate("state change"))

    async def _async_interval(self, _now: datetime) -> None:
        await self.async_evaluate("timer")

    def _temperature_c(self) -> float | None:
        state = self.hass.states.get(self.temperature_entity)
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        unit = str(state.attributes.get("unit_of_measurement", "°C"))
        if unit in {"°F", "F"}:
            return (value - 32.0) * 5.0 / 9.0
        return value

    def _heater_available(self) -> bool:
        state = self.hass.states.get(self.heater_switch)
        return state is not None and state.state not in {STATE_UNKNOWN, STATE_UNAVAILABLE}

    def _heater_is_on(self) -> bool:
        state = self.hass.states.get(self.heater_switch)
        return state is not None and state.state == STATE_ON

    async def _async_turn_on(self, reason: str) -> None:
        await self.hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": self.heater_switch},
            blocking=True,
        )
        self.heater_on_since = datetime.now(UTC)
        self.last_action = "turned_on"
        self.last_reason = reason

    async def _async_turn_off(self, reason: str) -> None:
        await self.hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": self.heater_switch},
            blocking=True,
        )
        self.heater_on_since = None
        self.last_action = "turned_off"
        self.last_reason = reason

    async def async_evaluate(self, trigger: str) -> None:
        """Evaluate temperature and safely control the backup heater."""
        now = datetime.now(UTC)
        self.last_evaluated = now.isoformat()

        if not self.enabled:
            self.status = "disabled"
            self.last_reason = "Backup heater mode is Off"
            self.below_since = None
            if self._heater_available() and self._heater_is_on():
                await self._async_turn_off(self.last_reason)
            self._notify()
            return

        if not self._heater_available():
            self.status = "switch_unavailable"
            self.last_reason = "Backup-heater switch unavailable"
            self._notify()
            return

        temperature = self._temperature_c()
        self.current_temperature_c = None if temperature is None else round(temperature, 2)
        heater_on = self._heater_is_on()

        if temperature is None:
            self.below_since = None
            self.status = "sensor_unavailable"
            self.last_reason = "Temperature sensor unavailable; fail-safe heater OFF"
            if heater_on:
                await self._async_turn_off(self.last_reason)
            self._notify()
            return

        if temperature < self.on_temp_c:
            if self.below_since is None:
                self.below_since = now
        else:
            self.below_since = None

        if heater_on and self.heater_on_since is None:
            self.heater_on_since = now
        if not heater_on:
            self.heater_on_since = None

        low_elapsed = (
            (now - self.below_since).total_seconds()
            if self.below_since is not None
            else None
        )
        on_elapsed = (
            (now - self.heater_on_since).total_seconds()
            if self.heater_on_since is not None
            else None
        )

        decision = decide_backup_heater(
            enabled=self.enabled,
            temperature_c=temperature,
            heater_on=heater_on,
            lockout=self.lockout,
            low_elapsed_seconds=low_elapsed,
            on_elapsed_seconds=on_elapsed,
            on_temp_c=self.on_temp_c,
            off_temp_c=self.off_temp_c,
            low_delay_seconds=self.low_delay.total_seconds(),
            max_runtime_seconds=self.max_runtime.total_seconds(),
        )

        self.lockout = decision.lockout
        self.status = decision.status
        self.last_reason = f"{decision.reason} ({trigger})"

        if temperature >= self.off_temp_c:
            self.below_since = None
            self.lockout = False

        if decision.action == "on" and not heater_on:
            await self._async_turn_on(decision.reason)
            self.status = "heating"
        elif decision.action == "off" and heater_on:
            await self._async_turn_off(decision.reason)

        self._notify()

    async def async_set_enabled(self, enabled: bool) -> None:
        """Set Auto/Off mode and apply it immediately."""
        self.enabled = bool(enabled)
        if not self.enabled:
            self.below_since = None
            if self._heater_available() and self._heater_is_on():
                await self._async_turn_off("Backup heater mode set to Off")
            self.status = "disabled"
            self.last_reason = "Backup heater mode is Off"
            self._notify()
            return
        await self.async_evaluate("mode changed")

    def _notify(self) -> None:
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_HEATER_UPDATED}_{self.entry_id}",
        )
