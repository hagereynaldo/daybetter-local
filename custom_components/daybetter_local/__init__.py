"""DayBetter Local: control de tiras DayBetter por la API local (UDP), sin nube."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, UPDATE_INTERVAL
from .hub import DayBetterHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["light"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura una entrada (una tira)."""
    data = hass.data.setdefault(DOMAIN, {})
    hub: DayBetterHub | None = data.get("hub")
    if hub is None:
        hub = DayBetterHub(hass)
        await hub.async_start()
        data["hub"] = hub

    ip: str = entry.data["ip"]

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _poll(_now=None) -> None:
        hub.async_request_status(ip)

    _poll()
    data.setdefault("unsubs", {})[entry.entry_id] = async_track_time_interval(
        hass, _poll, timedelta(seconds=UPDATE_INTERVAL)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Descarga la entrada."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        unsub = hass.data[DOMAIN].get("unsubs", {}).pop(entry.entry_id, None)
        if unsub is not None:
            unsub()
    return unload_ok
