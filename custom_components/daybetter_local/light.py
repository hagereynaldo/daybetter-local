"""Plataforma light para la API local de DayBetter."""
from __future__ import annotations

import colorsys
import time
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, UNAVAILABLE_AFTER
from .hub import DayBetterHub, signal_update

ATTR_BRIGHTNESS = "brightness"
ATTR_HS_COLOR = "hs_color"


def _rgb_to_hs(rgb: tuple[int, int, int]) -> tuple[float, float]:
    r, g, b = (c / 255 for c in rgb)
    h, s, _v = colorsys.rgb_to_hsv(r, g, b)
    return (round(h * 360, 3), round(s * 100, 3))


def _hs_to_rgb(hs: tuple[float, float]) -> tuple[int, int, int]:
    h, s = hs
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, max(0.0, min(s, 100.0)) / 100, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Crea la luz a partir de la entrada de configuración."""
    hub: DayBetterHub = hass.data[DOMAIN]["hub"]
    async_add_entities([DayBetterLocalLight(hub, entry)])


class DayBetterLocalLight(LightEntity):
    """Tira RGB controlada por la API local (sin nube)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_color_mode = ColorMode.HS
    _attr_supported_color_modes = {ColorMode.HS}

    def __init__(self, hub: DayBetterHub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._entry = entry
        self._ip: str = entry.data["ip"]
        self._sku: str = entry.data.get("sku") or "desconocido"
        self._attr_name = entry.data.get("name") or "Mirror LED"
        self._attr_unique_id = f"daybetter_local_{entry.data.get('device') or self._ip}"
        self._optimistic: dict[str, Any] = {}

    # ------------------------------------------------------------------ device
    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=self._attr_name,
            manufacturer="DayBetter",
            model=f"API local (SKU {self._sku})",
        )

    @property
    def _state(self) -> dict[str, Any] | None:
        return self._hub.states.get(self._ip)

    @property
    def available(self) -> bool:
        state = self._state
        if state is None:
            return False
        return (time.time() - float(state.get("last_seen") or 0)) < UNAVAILABLE_AFTER

    @property
    def is_on(self) -> bool:
        if "on" in self._optimistic:
            return bool(self._optimistic["on"])
        state = self._state
        return bool(state.get("on")) if state else False

    @property
    def brightness(self) -> int | None:
        if "brightness" in self._optimistic:
            return int(self._optimistic["brightness"])
        state = self._state
        if not state:
            return None
        return round(int(state.get("brightness") or 0) * 255 / 100)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        if "hs" in self._optimistic:
            return self._optimistic["hs"]
        state = self._state
        if not state:
            return None
        rgb = state.get("rgb") or (0, 0, 0)
        if tuple(rgb) == (0, 0, 0):
            return None
        return _rgb_to_hs(tuple(rgb))

    # ------------------------------------------------------------------ control
    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        hs_color = kwargs.get(ATTR_HS_COLOR)

        self._optimistic["on"] = True
        self._hub.async_turn(self._ip, True)

        if hs_color is not None:
            hs = (float(hs_color[0]), float(hs_color[1]))
            self._optimistic["hs"] = hs
            self._hub.async_color(self._ip, _hs_to_rgb(hs))

        if brightness is not None:
            self._optimistic["brightness"] = int(brightness)
            self._hub.async_brightness(self._ip, round(int(brightness) * 100 / 255))
        elif hs_color is not None:
            self._hub.async_brightness(self._ip, 100)
            self._optimistic["brightness"] = 255

        self.async_write_ha_state()
        self._hub.async_request_status(self._ip)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._optimistic["on"] = False
        self._hub.async_turn(self._ip, False)
        self.async_write_ha_state()
        self._hub.async_request_status(self._ip)

    # ------------------------------------------------------------------- update
    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._ip), self._handle_update
            )
        )
        self._hub.async_request_status(self._ip)

    @callback
    def _handle_update(self) -> None:
        # El estado real manda: se descarta lo optimista
        self._optimistic.clear()
        self.async_write_ha_state()
