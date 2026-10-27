"""HomeKit Camera Live: live video for Home Assistant's HomeKit cameras.

Home Assistant's HomeKit Controller integration shows a HomeKit camera as
snapshots only. This gives those same camera entities a live stream, through a
private go2rtc that opens the camera over HomeKit with the pairing Home
Assistant already has, and asks for snapshots at sizes the camera serves.
Nothing is paired again, and no entities are added.
"""

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.importlib import async_import_module

from .cameras import CAMERA_MODULE, Cameras
from .const import CONF_SRTP_PORT, DEFAULT_SRTP_PORT, DOMAIN
from .server import Go2rtcServer, async_find_binary

_LOGGER = logging.getLogger(__name__)


@dataclass
class Live:
    """What a running entry holds."""

    server: Go2rtcServer
    cameras: Cameras


type LiveConfigEntry = ConfigEntry[Live]


async def async_setup_entry(hass: HomeAssistant, entry: LiveConfigEntry) -> bool:
    """Start the private go2rtc and give the HomeKit cameras live view."""
    binary = await async_find_binary(hass)
    if binary is None:
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="no_go2rtc")
    try:
        # Off the event loop, so later imports of it are free.
        await async_import_module(hass, CAMERA_MODULE)
    except ImportError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="no_homekit_controller"
        ) from err
    server = Go2rtcServer(
        hass, binary, entry.options.get(CONF_SRTP_PORT, DEFAULT_SRTP_PORT)
    )
    cameras = Cameras(hass, server)
    try:
        await server.async_start(cameras.all_streams())
    except Exception as err:
        await server.async_stop()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="go2rtc_not_started",
            translation_placeholders={"error": str(err)},
        ) from err
    try:
        cameras.install()
        await cameras.async_refresh()
    except Exception:
        # Setup failed, so unload won't run: undo it here.
        cameras.uninstall()
        await server.async_stop()
        raise
    entry.runtime_data = Live(server, cameras)

    async def stop(_event: Event) -> None:
        await server.async_stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LiveConfigEntry) -> bool:
    """Put the cameras back as they were and stop go2rtc."""
    live = entry.runtime_data
    live.cameras.uninstall()
    try:
        await live.cameras.async_refresh()
    finally:
        await live.server.async_stop()
    return True


async def _options_changed(hass: HomeAssistant, entry: LiveConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
