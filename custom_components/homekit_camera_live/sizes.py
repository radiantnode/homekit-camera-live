"""Snapshot sizes a HomeKit camera actually serves.

A dashboard card asks for a snapshot at its own size (996x561, 498x280), and
homekit_controller passes that size straight to the camera. The size is only a
hint in HomeKit, and many cameras serve only the sizes they advertise: asked
for any other, some refuse and some (Aqara's) never answer, which shows as a
broken picture after Home Assistant's ten-second timeout.

So a snapshot is asked for at the smallest advertised size, in the camera's
own shape, that covers what was asked; Home Assistant then scales it to the
requested width and height, as it already does for every camera.
"""

import base64

#: Asked with no size at all (a thumbnail, the snapshot service), the widest
#: advertised size up to this.
UNSIZED_WIDEST = 1280


def _tlv(data: bytes) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    i, continued = 0, False
    while i + 2 <= len(data):
        kind, length = data[i], data[i + 1]
        value = data[i + 2 : i + 2 + length]
        if continued and out[-1][0] == kind:
            # A value over 255 bytes comes as fragments of the same type.
            out[-1] = (kind, out[-1][1] + value)
        else:
            out.append((kind, value))
        continued = length == 255
        i += 2 + length
    return out


def advertised_sizes(value) -> set[tuple[int, int]]:
    """Return every width and height in a Supported Video Stream Configuration.

    Live, aiohomekit hands it over decoded (`SupportedVideoStreamConfiguration`);
    cached on disk it is base64 TLV: codec configurations (1), each with its
    video attributes (3).
    """
    if hasattr(value, "config"):
        return {
            (a.width, a.height) for c in value.config or [] for a in c.video_attrs or []
        }
    raw = base64.b64decode(value) if isinstance(value, str) else bytes(value or b"")
    sizes = set()
    for kind, codec in _tlv(raw):
        if kind != 1:
            continue
        for kind2, attrs in _tlv(codec):
            if kind2 == 3:
                fields = dict(_tlv(attrs))
                if 1 in fields and 2 in fields:
                    sizes.add(
                        (
                            int.from_bytes(fields[1], "little"),
                            int.from_bytes(fields[2], "little"),
                        )
                    )
    return sizes


def pick_size(
    sizes: set[tuple[int, int]], width: int | None, height: int | None
) -> tuple[int, int] | None:
    """Return the size to ask the camera for.

    That's the smallest advertised size, in the camera's own shape, that
    covers width x height, or the largest of that shape when none does. With
    no size asked, it's the widest up to UNSIZED_WIDEST. None when nothing is
    advertised.
    """
    sizes = {s for s in sizes if s[0] > 0 and s[1] > 0}
    if not sizes:
        return None
    native = max(sizes, key=lambda s: s[0] * s[1])
    shape = [s for s in sizes if abs(s[0] / s[1] - native[0] / native[1]) < 0.01]
    if not width or not height:
        fitting = [s for s in shape if s[0] <= UNSIZED_WIDEST]
        return max(fitting or shape, key=lambda s: s[0])
    covering = [s for s in shape if s[0] >= width and s[1] >= height]
    if covering:
        return min(covering, key=lambda s: s[0] * s[1])
    return max(shape, key=lambda s: s[0] * s[1])
