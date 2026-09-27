"""Config flow: descubre tiras DayBetter con la API local activada."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from .const import DOMAIN, SCAN_TIMEOUT
from .hub import async_discover

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Mirror LED"


class DayBetterLocalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Flujo de configuración."""

    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, dict[str, Any]] = {}

    async def _scan(self, extra_ips: list[str] | None = None) -> bool:
        self._found = await async_discover(self.hass, SCAN_TIMEOUT, extra_ips)
        _LOGGER.debug("DayBetter local: encontrados %s", list(self._found))
        return bool(self._found)

    async def async_step_user(self, user_input=None):
        """Primer paso: escanear la red (y permitir reintentar, incluso por IP)."""
        extra_ips: list[str] = []
        if user_input:
            if ip := (user_input.get("ip") or "").strip():
                extra_ips.append(ip)

        if await self._scan(extra_ips):
            return await self.async_step_pick()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional("ip", default=""): str}),
            errors={"base": "no_devices"},
        )

    async def async_step_pick(self, user_input=None):
        """Segundo paso: elegir dispositivo y nombre."""
        options = {
            ip: f"{info.get('sku') or 'SKU desconocido'} · {ip}"
            for ip, info in self._found.items()
        }

        if user_input is not None:
            ip = user_input["ip"]
            info = self._found.get(ip, {})
            await self.async_set_unique_id(
                f"daybetter_local_{info.get('device') or ip}"
            )
            self._abort_if_unique_id_configured()
            name = (user_input.get("name") or DEFAULT_NAME).strip() or DEFAULT_NAME
            return self.async_create_entry(
                title=name,
                data={
                    "ip": ip,
                    "sku": info.get("sku"),
                    "device": info.get("device"),
                    "name": name,
                },
            )

        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required("ip"): vol.In(options),
                    vol.Optional("name", default=DEFAULT_NAME): str,
                }
            ),
            description_placeholders={"count": str(len(options))},
        )
