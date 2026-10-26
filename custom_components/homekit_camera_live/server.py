"""A private go2rtc that speaks HomeKit.

Home Assistant's own go2rtc runs without go2rtc's `homekit` module, so it can't
open a HomeKit camera's video. This runs a second go2rtc from the same binary
with only what HomeKit streaming needs: the HomeKit source, the SRTP port the
camera sends video to, an RTSP server on localhost for Home Assistant to read
from, and ffmpeg to turn HomeKit's AAC-ELD audio into something browsers play.

Its config holds HomeKit pairing keys, so it's written to a private temporary
folder. Nothing listens beyond localhost but the SRTP port, which only takes
video for sessions this go2rtc started. go2rtc opens a camera only while
something is reading its stream.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import socket
import tempfile

from homeassistant.core import HomeAssistant

from .const import RESTART_DELAYS_S, START_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)

#: What go2rtc prints once its RTSP server is up.
READY = "[rtsp] listen addr="
MODULES = ["api", "rtsp", "homekit", "exec", "ffmpeg", "srtp"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _q(value: str) -> str:
    # JSON strings are valid YAML scalars.
    return json.dumps(value)


def render_config(rtsp_port: int, srtp_port: int, streams: dict[str, list[str]]) -> str:
    """go2rtc's config: localhost-only RTSP, no API listener, SRTP for the
    cameras, and the streams."""
    lines = [
        "# Written by the HomeKit Camera Live integration; do not edit.",
        f"app:\n  modules: {json.dumps(MODULES)}",
        'api:\n  listen: ""',
        f"rtsp:\n  listen: {_q(f'127.0.0.1:{rtsp_port}')}",
        f"srtp:\n  listen: {_q(f':{srtp_port}')}",
        "exec:\n  allow_paths:\n    - ffmpeg",
        "log:\n  format: text",
        "streams:" + ("" if streams else " {}"),
    ]
    for name, sources in streams.items():
        lines.append(f"  {name}:")
        lines.extend(f"    - {_q(source)}" for source in sources)
    return "\n".join(lines) + "\n"


async def async_find_binary(hass: HomeAssistant) -> str | None:
    """The go2rtc binary Home Assistant's own go2rtc integration uses."""
    return await hass.async_add_executor_job(shutil.which, "go2rtc")


class Go2rtcServer:
    """Starts, restarts and stops the private go2rtc."""

    def __init__(self, hass: HomeAssistant, binary: str, srtp_port: int) -> None:
        self._hass = hass
        self._binary = binary
        self._srtp_port = srtp_port
        self._rtsp_port = 0
        self._folder: str | None = None
        self._streams: dict[str, list[str]] = {}
        self._process: asyncio.subprocess.Process | None = None
        self._watcher: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self._stopping = False

    def url(self, name: str) -> str:
        return f"rtsp://127.0.0.1:{self._rtsp_port}/{name}"

    @property
    def streams(self) -> dict[str, list[str]]:
        return dict(self._streams)

    async def async_start(self, streams: dict[str, list[str]]) -> None:
        self._streams = dict(streams)
        self._folder = await self._hass.async_add_executor_job(self._make_folder)
        async with self._lock:
            await self._launch()

    async def async_set_streams(self, streams: dict[str, list[str]]) -> None:
        """Run with these streams, restarting go2rtc only if they changed. A
        restart cuts off anyone watching, so it's left for a real change: a
        camera that moved address, or one added since."""
        if streams == self._streams:
            return
        async with self._lock:
            if streams == self._streams:
                return
            self._streams = dict(streams)
            await self._terminate()
            await self._launch()

    async def async_stop(self) -> None:
        self._stopping = True
        async with self._lock:
            await self._terminate()
        if self._folder:
            await self._hass.async_add_executor_job(shutil.rmtree, self._folder, True)

    @staticmethod
    def _make_folder() -> str:
        folder = tempfile.mkdtemp(prefix="homekit_camera_live-")
        os.chmod(folder, 0o700)
        return folder

    def _write_config(self) -> str:
        path = os.path.join(self._folder, "go2rtc.yaml")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(render_config(self._rtsp_port, self._srtp_port, self._streams))
        return path

    async def _launch(self) -> None:
        # Kept across restarts, so the address Home Assistant has stays good.
        if not self._rtsp_port:
            self._rtsp_port = await self._hass.async_add_executor_job(_free_port)
        path = await self._hass.async_add_executor_job(self._write_config)
        self._ready.clear()
        self._process = await asyncio.create_subprocess_exec(
            self._binary, "-c", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, close_fds=True,
        )
        process = self._process
        self._watcher = self._hass.async_create_background_task(
            self._watch(process), "homekit_camera_live go2rtc"
        )
        try:
            async with asyncio.timeout(START_TIMEOUT_S):
                await self._ready.wait()
        except TimeoutError:
            await self._terminate()
            raise RuntimeError(f"go2rtc didn't start within {START_TIMEOUT_S}s") from None
        _LOGGER.debug("go2rtc up: RTSP on 127.0.0.1:%s, SRTP on :%s, %s streams",
                      self._rtsp_port, self._srtp_port, len(self._streams))

    async def _watch(self, process: asyncio.subprocess.Process) -> None:
        """Pass go2rtc's output to the log, and start it again if it exits
        while nobody asked it to."""
        assert process.stdout
        async for raw in process.stdout:
            line = raw.decode(errors="replace").rstrip()
            if READY in line:
                self._ready.set()
            if " ERR " in line or " WRN " in line:
                _LOGGER.warning("go2rtc: %s", line)
            else:
                _LOGGER.debug("go2rtc: %s", line)
        code = await process.wait()
        if self._stopping or process is not self._process:
            return
        _LOGGER.warning("go2rtc exited (%s); starting it again", code)
        for delay in RESTART_DELAYS_S:
            await asyncio.sleep(delay)
            if self._stopping or process is not self._process:
                return
            try:
                async with self._lock:
                    if process is self._process:
                        await self._launch()
                return
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning("go2rtc didn't start again", exc_info=True)
        _LOGGER.error("go2rtc keeps exiting; HomeKit live view is off until the integration is reloaded")

    async def _terminate(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            async with asyncio.timeout(5):
                await process.wait()
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
