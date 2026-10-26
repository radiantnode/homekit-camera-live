"""HomeKit Camera Live: live video for Home Assistant's HomeKit cameras.

Home Assistant's HomeKit Controller integration shows a HomeKit camera as
snapshots only. This gives those same camera entities a live stream, through a
private go2rtc that opens the camera over HomeKit with the pairing Home
Assistant already has, and asks for snapshots at sizes the camera serves.
Nothing is paired again, and no entities are added.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .cameras import Cameras
from .const import CONF_SRTP_PORT, DEFAULT_SRTP_PORT
from .server import Go2rtcServer, async_find_binary

_LOGGER = logging.getLogger(__name__)


@dataclass
class Live:
    server: Go2rtcServer
    cameras: Cameras


type LiveConfigEntry = ConfigEntry[Live]


async def async_setup_entry(hass: HomeAssistant, entry: LiveConfigEntry) -> bool:
    binary = await async_find_binary(hass)
    if binary is None:
        raise ConfigEntryNotReady("No go2rtc binary on this system; see the README")
    server = Go2rtcServer(hass, binary, entry.options.get(CONF_SRTP_PORT, DEFAULT_SRTP_PORT))
    cameras = Cameras(hass, server)
    try:
        await server.async_start(cameras.all_streams())
    except Exception as err:
        await server.async_stop()
        raise ConfigEntryNotReady(f"go2rtc didn't start: {err}") from err
    cameras.install()
    await cameras.async_refresh()
    entry.runtime_data = Live(server, cameras)

    async def stop(_event: Event) -> None:
        await server.async_stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LiveConfigEntry) -> bool:
    live = entry.runtime_data
    live.cameras.uninstall()
    await live.cameras.async_refresh()
    await live.server.async_stop()
    return True


async def _options_changed(hass: HomeAssistant, entry: LiveConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
