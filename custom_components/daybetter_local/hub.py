"""Transporte UDP y estado para la API local de DayBetter.

Protocolo oficial (daybetter-local-api, Apache-2.0):
  - descubrimiento: JSON {"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}} -> UDP 6281
  - comandos:       JSON {"msg":{"cmd":...,"data":{...}}}                        -> UDP 6283
  - estado:         cmd "devStatus" responde onOff/color/brightness/colorTemInKelvin
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import get_url

from .const import COMMAND_PORT, DOMAIN, SCAN_PORT, SCAN_TIMEOUT

_LOGGER = logging.getLogger(__name__)


def signal_update(ip: str) -> str:
    """Señal de dispatcher para una IP concreta."""
    return f"{DOMAIN}_update_{ip}"


def _scan_bytes() -> bytes:
    return json.dumps(
        {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}
    ).encode()


def _cmd_bytes(cmd: str, data: dict[str, Any]) -> bytes:
    return json.dumps({"msg": {"cmd": cmd, "data": data}}).encode()


class _DayBetterProtocol(asyncio.DatagramProtocol):
    """Recibe las respuestas de los dispositivos."""

    def __init__(
        self,
        on_device: Callable[[str, dict[str, Any]], None],
        on_status: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._on_device = on_device
        self._on_status = on_status

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            message = json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return

        body = message.get("msg") or {}
        cmd = body.get("cmd")
        payload = body.get("data") or {}
        ip = addr[0]

        if not isinstance(payload, dict):
            return

        if cmd == "scan":
            self._on_device(ip, payload)
        elif cmd == "devStatus":
            self._on_status(ip, payload)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        _LOGGER.debug("Error UDP: %s", exc)


class DayBetterHub:
    """Un único socket UDP compartido por todas las entradas."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.transport: asyncio.DatagramTransport | None = None
        self.devices: dict[str, dict[str, Any]] = {}
        self.states: dict[str, dict[str, Any]] = {}

    async def async_start(self) -> None:
        self.transport, _ = await self.hass.loop.create_datagram_endpoint(
            lambda: _DayBetterProtocol(self._handle_device, self._handle_status),
            local_addr=("0.0.0.0", 0),
            allow_broadcast=True,
        )

    @callback
    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None

    # ------------------------------------------------------------------ envío
    def send(self, payload: bytes, ip: str) -> None:
        if self.transport is None:
            return
        try:
            self.transport.sendto(payload, (ip, COMMAND_PORT))
        except OSError as err:  # pragma: no cover
            _LOGGER.debug("No se pudo enviar a %s: %s", ip, err)

    def async_turn(self, ip: str, on: bool) -> None:
        self.send(_cmd_bytes("turn", {"value": 1 if on else 0}), ip)

    def async_brightness(self, ip: str, percent: int) -> None:
        self.send(_cmd_bytes("brightness", {"value": max(0, min(int(percent), 100))}), ip)

    def async_color(self, ip: str, rgb: tuple[int, int, int]) -> None:
        r, g, b = (max(0, min(int(c), 255)) for c in rgb)
        self.send(
            _cmd_bytes(
                "colorwc",
                {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0},
            ),
            ip,
        )

    def async_request_status(self, ip: str) -> None:
        self.send(_cmd_bytes("devStatus", {}), ip)

    # ------------------------------------------------------------- recepción
    @callback
    def _handle_device(self, ip: str, payload: dict[str, Any]) -> None:
        self.devices[ip] = {
            "ip": payload.get("ip") or ip,
            "sku": payload.get("sku"),
            "device": payload.get("device") or ip,
        }
        _LOGGER.debug("Dispositivo DayBetter visto: %s", self.devices[ip])

    @callback
    def _handle_status(self, ip: str, payload: dict[str, Any]) -> None:
        color = payload.get("color") or {}
        if not isinstance(color, dict):
            color = {}
        self.states[ip] = {
            "on": bool(payload.get("onOff")),
            "brightness": int(payload.get("brightness") or 0),
            "rgb": (
                int(color.get("r") or 0),
                int(color.get("g") or 0),
                int(color.get("b") or 0),
            ),
            "temp_k": int(payload.get("colorTemInKelvin") or 0),
            "last_seen": time.time(),
        }
        async_dispatcher_send(self.hass, signal_update(ip))

    # --------------------------------------------------------- descubrimiento
    async def _async_subnets(self) -> list[str]:
        """Prefijos /24 candidatos, sacados de la URL de HA y de sus interfaces."""
        hosts: list[str] = []
        for prefer_external in (True, False):
            try:
                hosts.append(get_url(self.hass, prefer_external=prefer_external))
            except Exception:  # noqa: BLE001 - sin URL configurada
                pass
        try:
            # El contenedor de HA está en NAT: sus propias interfaces no son la LAN,
            # pero la URL interna/externa sí suele dar la IP real de la casa.
            from homeassistant.components.network import async_get_adapters

            for adapter in await async_get_adapters(self.hass):
                for ipv4 in adapter.get("ipv4") or []:
                    if address := ipv4.get("address"):
                        hosts.append(str(address))
        except Exception as err:  # noqa: BLE001 - integración network opcional
            _LOGGER.debug("Sin adaptadores de red: %s", err)

        prefixes: set[str] = set()
        for raw in hosts:
            host = raw.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
            if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host) and not host.startswith("127."):
                prefixes.add(".".join(host.split(".")[:3]))
        _LOGGER.debug("Subredes a barrer: %s", sorted(prefixes))
        return sorted(prefixes)

    async def _async_scan_targets(self, extra_ips: list[str] | None = None) -> list[tuple[str, int]]:
        targets: list[tuple[str, int]] = [("255.255.255.255", SCAN_PORT)]
        for prefix in await self._async_subnets():
            targets.extend((f"{prefix}.{i}", SCAN_PORT) for i in range(1, 255))
        for ip in extra_ips or []:
            ip = ip.strip()
            if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip):
                targets.append((ip, SCAN_PORT))
        return targets

    async def async_scan(
        self, timeout: float = SCAN_TIMEOUT, extra_ips: list[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Escaneo: broadcast + barrido /24 + IPs concretas. Envía dos rondas."""
        self.devices.clear()
        if self.transport is None:
            return {}
        payload = _scan_bytes()
        targets = await self._async_scan_targets(extra_ips)
        for round_no in range(2):
            for target in targets:
                try:
                    self.transport.sendto(payload, target)
                except OSError:  # pragma: no cover
                    pass
            if round_no == 0:
                await asyncio.sleep(timeout / 2)
        await asyncio.sleep(timeout / 2)
        return dict(self.devices)


async def async_discover(
    hass: HomeAssistant, timeout: float = SCAN_TIMEOUT, extra_ips: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Escaneo puntual con socket temporal (usado por el config flow)."""
    hub = DayBetterHub(hass)
    await hub.async_start()
    try:
        return await hub.async_scan(timeout, extra_ips)
    finally:
        hub.close()
