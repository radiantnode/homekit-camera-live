# HomeKit Camera Live

Live video for the cameras you've paired with Home Assistant's
[HomeKit Controller](https://www.home-assistant.io/integrations/homekit_controller/)
integration.

HomeKit Controller shows a HomeKit camera as snapshots only: open one and you get
a slideshow of stills, a frame or two a second. With this installed, the same
camera entities play live video (H.264 and audio, WebRTC in the browser), and
their snapshots stop breaking on dashboards.

It uses the pairing Home Assistant already has, so nothing is paired again. It
adds no entities either: your existing `camera.` entities gain live view, and
dashboards, cards and automations keep working as they are. There's nothing to
configure, and one integration covers every HomeKit camera.

Tested with an Aqara Camera Hub G3 and an Aqara Video Doorbell G4, on Home
Assistant 2026.9: both play 1920×1080 at 20 fps, with the first frame in about 3
seconds.

## How it works

Home Assistant ships [go2rtc](https://github.com/AlexxIT/go2rtc), which can stream
a HomeKit camera, but Home Assistant runs it without go2rtc's HomeKit module. This
integration starts a second, private go2rtc from the same binary with HomeKit
turned on, and gives each HomeKit camera entity a stream from it:

```
camera ──HomeKit (SRTP)──▶ private go2rtc ──RTSP, localhost──▶ Home Assistant's go2rtc ──WebRTC──▶ browser
```

- go2rtc opens a camera only while something is watching it, and closes it when
  the last viewer leaves.
- HomeKit cameras send AAC-ELD audio, which browsers can't play; go2rtc adds an
  Opus copy with ffmpeg, made only while someone is listening.
- Snapshots are asked for at a size the camera serves. Dashboards ask for a
  card's own size (say 996×561), Home Assistant passes it straight to the camera,
  and many HomeKit cameras only answer the sizes they advertise. Some refuse the
  others and some never answer, which shows as a broken picture after ten
  seconds. This asks for the smallest advertised size, in the camera's own shape,
  that covers the request, and Home Assistant scales it down. (Home Assistant
  2026.11 adds a retry at 640×480 for cameras that refuse a size; that doesn't
  help cameras that don't answer.)

## Install

With HACS, add `https://github.com/radiantnode/homekit-camera-live` as a custom
repository (category *Integration*), install **HomeKit Camera Live**, and restart
Home Assistant.

By hand, copy `custom_components/homekit_camera_live` into your config folder's
`custom_components`, and restart Home Assistant.

Then **Settings > Devices & services > Add integration > HomeKit Camera Live**.
There's nothing to fill in. Open one of your HomeKit cameras and it plays live.

## Requirements

- Home Assistant 2026.9 or newer, with its built-in go2rtc (part of
  `default_config`). Home Assistant OS and the official container image include
  the `go2rtc` binary this uses. On other installs, put a `go2rtc` binary (1.9 or
  newer) on Home Assistant's `PATH`.
- UDP port 18443 has to be reachable from your cameras, which send their video
  to it. If something else uses that port, change it under the integration's
  **Configure**. With host networking (the usual setup for HomeKit) there's
  nothing to do; behind Docker's bridge network, publish the port.
- Cameras paired directly with HomeKit Controller. A camera behind a HomeKit
  *bridge* keeps snapshots only, because go2rtc streams a device's first
  accessory.

## Security

- The private go2rtc's config holds the cameras' HomeKit pairing keys. It's
  written to a temporary folder only Home Assistant's user can read, and deleted
  when the integration stops.
- go2rtc's API and web interface are turned off. Its RTSP server listens on
  localhost only.
- The one port open beyond localhost is the SRTP (video) port. It only takes video
  for streams this go2rtc set up.
- The pairing key is masked in any go2rtc output that reaches Home Assistant's
  log.

## Good to know

- Closing a live view doesn't stop the camera at once. Home Assistant keeps its own
  HLS fallback stream open for about a minute after the last viewer leaves, as it
  does for every streaming camera, and that keeps the camera streaming.
- If a camera changes address, its stream is updated the next time it's opened,
  which restarts the private go2rtc and briefly cuts off anyone watching another
  HomeKit camera.

## Remove

Go to **Settings > Devices & services > HomeKit Camera Live** and delete the
integration. That stops the private go2rtc and puts HomeKit Controller back
exactly as it was, without a restart. If you installed with HACS, remove it there
too.

## Troubleshooting

Turn on debug logging to see go2rtc's own output:

```yaml
logger:
  logs:
    custom_components.homekit_camera_live: debug
```

go2rtc's warnings and errors are logged even without it.

## Credits

[go2rtc](https://github.com/AlexxIT/go2rtc) by Alex X does the HomeKit streaming.
This integration starts it and connects it to Home Assistant.

## License

MIT
