"""Constants for HomeKit Camera Live."""

DOMAIN = "homekit_camera_live"

#: The UDP port the camera sends its video to (SRTP). It has to be reachable
#: from the cameras, so it is the one port this listens on beyond localhost.
#: Not go2rtc's own default (8443), so it can't collide with a go2rtc add-on.
CONF_SRTP_PORT = "srtp_port"
DEFAULT_SRTP_PORT = 18443

#: How long go2rtc gets to start before setup gives up.
START_TIMEOUT_S = 30
#: Waits between restarts after go2rtc exits on its own.
RESTART_DELAYS_S = (1, 5, 15, 60)
