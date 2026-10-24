"""Constants for the Hikvision NVR Away Arm integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "hikvision_away"

CONF_USE_SSL: Final = "use_ssl"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_CHANNELS: Final = "channels"
CONF_CHANNEL_NAMES: Final = "channel_names"
CONF_DEVICE_SERIAL: Final = "device_serial"
CONF_NAME: Final = "name"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_HOST: Final = ""
DEFAULT_PORT: Final = 80
DEFAULT_USERNAME: Final = "admin"
DEFAULT_NAME: Final = "离家布防模式"
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 300

DEFAULT_USE_SSL: Final = False
DEFAULT_VERIFY_SSL: Final = False

# The user's NVR channels 7 and 8 are pre-selected when they are available.
# NVR GUI: 周界防范 -> 区域入侵检测 -> 联动方式 -> 上传中心
# ISAPI: /ISAPI/Event/triggers/fielddetection-<channel>
# notificationMethod: center
PREFERRED_CHANNELS: Final = (7, 8)
