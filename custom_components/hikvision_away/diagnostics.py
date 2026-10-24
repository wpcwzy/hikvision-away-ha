"""Diagnostics support for the Hikvision NVR Away Arm integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .models import HikvisionAwayRuntimeData

TO_REDACT = {CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime: HikvisionAwayRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    channel_data: dict[str, dict[str, Any]] = {}
    if coordinator.data is not None:
        for channel in coordinator.data.channels:
            channel_data[str(channel.channel)] = {
                "upload_center": channel.upload_center,
                "notification_methods": list(channel.notification_methods),
            }
    return {
        "entry": {
            "title": entry.title,
            "unique_id": entry.unique_id,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "device": asdict(runtime.device),
        "channels": [
            {"id": channel, "name": name}
            for channel, name in zip(
                runtime.channels, runtime.channel_names, strict=False
            )
        ],
        "switch_name": runtime.name,
        "isapi": {
            "base_url": runtime.client.base_url,
            "endpoints": [
                f"/ISAPI/Event/triggers/fielddetection-{channel}"
                for channel in runtime.channels
            ],
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval is not None
                else None
            ),
            "channel_data": channel_data,
        },
    }
