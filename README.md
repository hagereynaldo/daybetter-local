# DayBetter Local (LAN) — Home Assistant integration

Control your DayBetter LED strips **entirely over your local network** — no cloud, no vendor app, no account. Works with the official [DayBetter local API](https://github.com/THDayBetter/daybetter-local-api) (Apache-2.0) over UDP.

If your strip stops working when the internet goes down, or you just don't want a light bulb talking to a server in another country, this is the integration you're looking for.

---

## Why this exists

DayBetter strips ship with a cloud app. That means:

- Your lights stop responding if the vendor's servers go down or the internet drops
- Every on/off command leaves your house and comes back
- If the vendor shuts down the service, your hardware becomes e-waste

DayBetter also exposes a **documented local UDP API**. This integration talks to that API directly. Once set up, nothing leaves your LAN.

## Features

| | |
|---|---|
| **Local only** | All traffic stays on your network. `iot_class: local_polling` |
| **Automatic discovery** | Two-step config flow with network scan — no manual IP hunting in the common case |
| **UI configurable** | Full config flow, no YAML editing |
| **Translations** | English and Spanish |
| **Honest state** | Optimistic updates for instant UI feedback, reconciled against the device's real state |
| **Availability tracking** | The entity reports *unavailable* when the strip stops responding, instead of lying to you |

---

## Installation

### HACS (recommended)

1. In HACS, go to **Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/hagereynaldo/daybetter-local` as an **Integration**
3. Install **DayBetter Local (LAN)**
4. Restart Home Assistant

### Manual

Copy the `custom_components/daybetter_local` folder into your Home Assistant `config/custom_components/` directory, then restart Home Assistant.

---

## Setup

1. Make sure the **local API is enabled** in the DayBetter app for each strip you want to control
2. In Home Assistant: **Settings → Devices & Services → Add Integration → DayBetter Local (LAN)**
3. The integration scans your network and lists what it finds
4. Pick your strip, give it a name, done

If nothing is found, the flow lets you enter the strip's IP directly and re-scan.

---

## The interesting part: discovery inside a container

This was the hardest problem in the integration, so it's worth explaining.

The local API has no mDNS, no SSDP, no service advertisement. The only way to find a strip is to **send a UDP scan packet and wait for it to answer**. The naive approach is to broadcast to `255.255.255.255` and hope.

That fails constantly in real setups, for three reasons:

**1. Broadcast does not cross subnets.** If your Home Assistant instance is on a different VLAN than your lights — or behind a router doing NAT — the broadcast never arrives.

**2. Home Assistant in Docker sees its own container network, not your LAN.** This is the subtle one. When HA runs in a bridge-mode container, asking the OS for its network interfaces returns the *container's* addresses (usually `172.17.x.x`), which tell you nothing about your actual home network. Scanning that range finds nothing.

**3. A single UDP datagram is fire-and-forget.** Some devices drop it, especially while waking from power saving. One scan round gives unreliable results.

### How this integration solves it

The strategy is **three sources of targets, sent twice**:

```
targets = broadcast
        + every /24 derived from a trusted network hint
        + any IP you typed in manually

for round in (1, 2):
    send scan packet to every target
    wait timeout / 2
```

The key insight is where the network hint comes from. Instead of trusting the OS interfaces — which are wrong in a container — the integration asks Home Assistant for its own configured URL:

```python
from homeassistant.helpers.network import get_url

for prefer_external in (True, False):
    hosts.append(get_url(self.hass, prefer_external=prefer_external))
```

Home Assistant's internal or external URL is almost always a real address on the real LAN (`http://192.168.1.50:8123`), so its `/24` prefix is a correct place to sweep. The integration uses **both** the URL hint and the OS adapters, because either can be right depending on your deployment:

- HA OS / bare metal → OS adapters are correct
- HA in Docker with a published port → the URL is correct
- Remote access configured → `prefer_external` catches the LAN address behind it

Private and loopback addresses are filtered out, prefixes are deduplicated, and the sweep is bounded to `/24` (254 addresses) per prefix so it stays fast.

Finally, the scan runs **twice**. Devices that missed the first datagram usually answer the second. This costs a few hundred milliseconds and turns "sometimes it doesn't find my lights" into "it always finds my lights."

---

## The other design decision: optimistic state, real reconciliation

The local API is request/response over UDP. There is no push, no subscription, no change notification. So after you press a button there are only two options:

- Wait for the device to confirm before updating the UI → feels broken and laggy
- Assume it worked → lies to you when it didn't

Neither is acceptable, so the integration does both, in order:

```python
# 1. Apply optimistically so the UI responds instantly
self._optimistic["on"] = True
self._hub.async_turn(self._ip, True)
self.async_write_ha_state()

# 2. Ask the device what its actual state is
self._hub.async_request_status(self._ip)
```

Then, when the real `devStatus` reply arrives over UDP:

```python
@callback
def _handle_update(self) -> None:
    # Real state wins: discard the optimistic guess
    self._optimistic.clear()
    self.async_write_ha_state()
```

The result: **instant feedback that corrects itself if the device disagrees.** If the strip was unreachable, the optimistic value gets wiped within milliseconds and the UI reflects reality. No phantom "on" states, which is the single most common complaint about local integrations for cloud-first hardware.

Availability is handled the same way — the entity carries a `last_seen` timestamp and reports `unavailable` once it exceeds a threshold:

```python
return (time.time() - float(state["last_seen"])) < UNAVAILABLE_AFTER
```

A light that isn't answering is not a light that is off. Saying so plainly is more useful than pretending.

---

## Protocol reference

The integration implements the official protocol, documented here: **[THDayBetter/daybetter-local-api](https://github.com/THDayBetter/daybetter-local-api)** (Apache-2.0).

| Action | Port | Message |
|---|---|---|
| Discovery scan | 6281 | `{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}` |
| Commands | 6283 | `{"msg":{"cmd":...,"data":{...}}}` |

Commands used:

| Command | Payload | Effect |
|---|---|---|
| `turn` | `{"value": 0\|1}` | Power |
| `brightness` | `{"value": 0-100}` | Brightness as a percentage |
| `colorwc` | `{"color":{"r":..,"g":..,"b":..},"colorTemInKelvin":0}` | RGB color |
| `devStatus` | `{}` | Requests current state |

State arrives as `devStatus` with `onOff`, `brightness` (0–100), `color.{r,g,b}` and `colorTemInKelvin`.

Note the unit mismatch the integration handles for you: **the device speaks brightness 0–100, Home Assistant speaks 0–255.**

---

## Requirements

- Home Assistant 2024.x or newer
- A DayBetter device with the **local API enabled**
- Home Assistant and the strip on the **same L2 network** for automatic discovery (otherwise enter the IP manually)

## Notes and limitations

- **RGB only.** The protocol carries a color-temperature field, but this integration currently exposes `ColorMode.HS`. White-balance control would need a `ColorMode.COLOR_TEMP` path.
- **Polling, not push.** State refreshes when you act and when the entity is added. There is no background poll loop because the API has no change notification; a periodic poll could be added with a configurable interval.
- **No cloud fallback, by design.** If discovery fails, that's a network problem worth seeing, not something to paper over.

## Contributing

Issues and pull requests are welcome. If your strip isn't discovered automatically, please open an issue with:

- Your Home Assistant installation type (HA OS, Docker, Supervised, Core)
- Whether `get_url()` returns a LAN address in your setup
- The `sku` your device reports, if it appears in the scan

That information makes discovery bugs fixable instead of guesswork.

## License

MIT — see [LICENSE](LICENSE).

The DayBetter local API protocol itself is documented by [THDayBetter/daybetter-local-api](https://github.com/THDayBetter/daybetter-local-api) under Apache-2.0. This project is an independent client implementation and is not affiliated with or endorsed by DayBetter.
