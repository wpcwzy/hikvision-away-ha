"""Switch platform for the Hikvision NVR Away Arm integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HikvisionAuthError, HikvisionError
from .const import DOMAIN
from .coordinator import HikvisionAwayCoordinator
from .models import HikvisionAwayRuntimeData

_LOGGER = logging.getLogger(__name__)

# ISAPI event ID used by the NVR for the region-intrusion / field-detection
# linkage of a channel: /ISAPI/Event/triggers/fielddetection-<channel>
FIELD_DETECTION_TRIGGER = "/ISAPI/Event/triggers/fielddetection-{channel}"


class HikvisionAwaySwitch(CoordinatorEntity[HikvisionAwayCoordinator], SwitchEntity):
    """Switch that enables/disables the upload-center link of one channel."""

    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        runtime: HikvisionAwayRuntimeData,
    ) -> None:
        super().__init__(runtime.coordinator)
        self._entry = entry
        self._runtime = runtime
        self._attr_name = runtime.name
        self._attr_unique_id = f"{entry.entry_id}_away_arm"

        identifier = runtime.device.serial_number or (
            f"{entry.data[CONF_HOST]}:{runtime.client.port}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=f"Hikvision NVR ({entry.data[CONF_HOST]})",
            manufacturer="Hikvision",
            model=runtime.device.model or None,
            sw_version=runtime.device.firmware_version or None,
            configuration_url=runtime.client.base_url,
        )

    @property
    def is_on(self) -> bool | None:
        """Return true only when all selected channels are armed."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.all_upload_center_enabled

    @property
    def icon(self) -> str:
        """Return a shield icon that reflects the arm state."""
        return "mdi:shield-home" if self.is_on else "mdi:shield-home-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return useful diagnostic attributes."""
        data = self.coordinator.data
        return {
            "nvr_host": self._entry.data[CONF_HOST],
            "nvr_port": self._runtime.client.port,
            "channels": list(self._runtime.channels),
            "channel_names": list(self._runtime.channel_names),
            "channel_states": data.channel_states if data is not None else {},
            "event": "fielddetection",
            "isapi_endpoints": [
                FIELD_DETECTION_TRIGGER.format(channel=channel)
                for channel in self._runtime.channels
            ],
            "notification_methods": (
                data.notification_methods if data is not None else {}
            ),
            "upload_center": self.is_on,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable 周界防范 -> 区域入侵检测 -> 联动方式 -> 上传中心."""
        await self._async_set_upload_center(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable 周界防范 -> 区域入侵检测 -> 联动方式 -> 上传中心."""
        await self._async_set_upload_center(False)

    async def _async_set_upload_center(self, enabled: bool) -> None:
        try:
            await self.coordinator.async_set_upload_center(enabled)
        except HikvisionAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except HikvisionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_update",
                translation_placeholders={
                    "error": str(err),
                    "channels": ", ".join(str(c) for c in self._runtime.channels),
                },
            ) from err
        _LOGGER.debug(
            "Channels %s upload-center link set to %s",
            self._runtime.channels,
            enabled,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the away-arm switch."""
    runtime: HikvisionAwayRuntimeData = entry.runtime_data
    async_add_entities([HikvisionAwaySwitch(entry, runtime)])
