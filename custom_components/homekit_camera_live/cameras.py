"""What this adds to homekit_controller's camera entities.

- A live stream: `stream_source` points at the private go2rtc, which opens the
  camera over HomeKit with Home Assistant's own pairing, and the entity says it
  supports streaming. Home Assistant's go2rtc then serves it to the browser
  over WebRTC, and HLS works too.
- Snapshots at a size the camera serves (see sizes.py).

homekit_controller's camera class is changed in place, for every HomeKit
camera at once, and put back when the integration is removed.
"""

import importlib
import logging
from typing import Any

from homeassistant.components.camera import CameraEntityFeature
from homeassistant.components.camera.const import DATA_COMPONENT
from homeassistant.core import HomeAssistant

from .server import Go2rtcServer
from .sizes import advertised_sizes, pick_size

_LOGGER = logging.getLogger(__name__)

STREAM_MANAGEMENT = "00000110-0000-1000-8000-0026BB765291"
SUPPORTED_VIDEO = "00000114-0000-1000-8000-0026BB765291"
#: Where the class keeps what was there before, so it's never wrapped twice
#: and can be put back by whichever setup comes next.
SAVED = "_homekit_camera_live_saved"
CAMERA_MODULE = "homeassistant.components.homekit_controller.camera"


def _camera_class() -> type:
    # Imported by setup already (off the event loop), so this is a lookup.
    return importlib.import_module(CAMERA_MODULE).HomeKitCamera


def stream_name(pairing_id: str, aid: int) -> str:
    """The go2rtc stream name for an accessory."""
    return f"homekit_{pairing_id.replace(':', '').lower()}_{aid}"


def _url_host(host: str) -> str:
    # An IPv6 address needs brackets in a URL, and its zone's % escaped.
    return f"[{host.replace('%', '%25')}]" if ":" in host else host


def homekit_url(
    pairing_data: dict[str, Any], host: str | None = None, port: int | None = None
) -> str | None:
    """go2rtc's address for a HomeKit accessory, from Home Assistant's pairing.

    It's the same address go2rtc's own `hass:` source builds. None for a
    pairing that isn't over IP.
    """
    try:
        return (
            f"homekit://{_url_host(host or pairing_data['AccessoryIP'])}:{port or pairing_data['AccessoryPort']}"
            f"?client_id={pairing_data['iOSPairingId']}"
            f"&client_private={pairing_data['iOSDeviceLTSK']}{pairing_data['iOSDeviceLTPK']}"
            f"&device_id={pairing_data['AccessoryPairingID']}"
            f"&device_public={pairing_data['AccessoryLTPK']}"
        )
    except KeyError:
        return None


class Cameras:
    """The changes to HomeKitCamera, and the streams they need."""

    def __init__(self, hass: HomeAssistant, server: Go2rtcServer) -> None:
        """Keep the server the streams run on."""
        self._hass = hass
        self._server = server

    # ---- the stream ------------------------------------------------------

    def sources(self, camera) -> tuple[str, list[str]] | None:
        """Return this camera's stream name and go2rtc sources.

        None when the camera can't be streamed this way.
        """
        if camera._aid != 1:
            # go2rtc streams a HomeKit device's first accessory, so a camera
            # behind a HomeKit bridge isn't one it can reach.
            return None
        device = camera._accessory
        data = getattr(device, "pairing_data", None) or {}
        connection = getattr(device.pairing, "connection", None)
        url = homekit_url(
            data,
            getattr(connection, "connected_host", None),
            getattr(connection, "port", None),
        )
        if url is None:
            return None
        name = stream_name(data["AccessoryPairingID"], camera._aid)
        # HomeKit's audio is AAC-ELD, which browsers can't play; the second
        # source adds an Opus copy, made only when something asks for it.
        return name, [url, f"ffmpeg:{name}#audio=opus"]

    def all_streams(self) -> dict[str, list[str]]:
        """Return the streams for every HomeKit camera there is now."""
        streams: dict[str, list[str]] = {}
        for camera in self._homekit_cameras():
            if found := self.sources(camera):
                streams[found[0]] = found[1]
        return streams

    async def stream_source(self, camera) -> str | None:
        """Return the camera's RTSP address on the private go2rtc."""
        found = self.sources(camera)
        if found is None:
            return None
        name, sources = found
        # Restarts go2rtc only for a camera that's new since start-up, or one
        # with a new address.
        await self._server.async_set_stream(name, sources)
        return self._server.url(name)

    # ---- the snapshot ----------------------------------------------------

    @staticmethod
    def sizes(camera) -> set[tuple[int, int]]:
        """Return the snapshot sizes the camera advertises."""
        accessory = camera._accessory.entity_map.aid(camera._aid)
        sizes = set()
        for service in accessory.services:
            if service.type.upper() != STREAM_MANAGEMENT:
                continue
            for characteristic in service.characteristics:
                if (
                    characteristic.type.upper() == SUPPORTED_VIDEO
                    and characteristic.value
                ):
                    sizes |= advertised_sizes(characteristic.value)
        return sizes

    # ---- installing ------------------------------------------------------

    def install(self) -> None:
        """Change the class. Run again, it binds to this instance instead."""
        cls = _camera_class()
        if (saved := cls.__dict__.get(SAVED)) is None:
            saved = {"async_camera_image": cls.async_camera_image}
            setattr(cls, SAVED, saved)
        cameras = self
        original_image = saved["async_camera_image"]

        async def async_camera_image(
            camera, width: int | None = None, height: int | None = None
        ) -> bytes | None:
            try:
                size = pick_size(cameras.sizes(camera), width, height)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "Couldn't read %s's video sizes", camera.entity_id, exc_info=True
                )
                size = None
            if size is None:
                return await original_image(camera, width, height)
            image = await camera._accessory.pairing.image(camera._aid, *size)
            if image is None and size != (width, height):
                return await original_image(camera, width, height)
            return image

        async def stream_source(camera) -> str | None:
            return await cameras.stream_source(camera)

        def supported_features(camera) -> CameraEntityFeature:
            features = camera._attr_supported_features
            if cameras.sources(camera) is not None:
                features |= CameraEntityFeature.STREAM
            return features

        cls.async_camera_image = async_camera_image
        cls.stream_source = stream_source
        cls.supported_features = property(supported_features)

    def uninstall(self) -> None:
        """Put the class back as it was."""
        cls = _camera_class()
        if (saved := cls.__dict__.get(SAVED)) is None:
            return
        cls.async_camera_image = saved["async_camera_image"]
        # These two are inherited from Camera; removing ours restores them.
        for name in ("stream_source", "supported_features", SAVED):
            if name in cls.__dict__:
                delattr(cls, name)

    async def async_refresh(self) -> None:
        """Have Home Assistant look again at what each HomeKit camera streams.

        That turns live view on (or off) without a restart.
        """
        for camera in self._homekit_cameras():
            if camera.stream is not None:
                # Home Assistant keeps the first stream it made, which reads
                # from the go2rtc that was running then.
                await camera.stream.stop()
                camera.stream = None
            camera._invalidate_camera_capabilities_cache()
            await camera.async_refresh_providers(write_state=False)
            # The STREAM feature is in the state, and the provider may not
            # have changed (HLS only, without Home Assistant's go2rtc).
            camera.async_write_ha_state()

    def _homekit_cameras(self) -> list:
        component = self._hass.data.get(DATA_COMPONENT)
        if component is None:
            return []
        cls = _camera_class()
        return [entity for entity in component.entities if isinstance(entity, cls)]
