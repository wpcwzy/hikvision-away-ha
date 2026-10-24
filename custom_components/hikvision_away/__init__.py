"""Home Assistant integration for Hikvision NVR away-arm mode."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import (
    HikvisionAuthError,
    HikvisionError,
    HikvisionIsapiClient,
)
from .const import (
    CONF_CHANNEL_NAMES,
    CONF_CHANNELS,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_SSL,
    DEFAULT_VERIFY_SSL,
)
from .coordinator import HikvisionAwayCoordinator
from .models import HikvisionAwayRuntimeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH]


def _normalise_channels(raw_channels: object) -> list[int]:
    """Return a sorted, de-duplicated list of channel numbers."""
    if raw_channels is None:
        return []
    channels: set[int] = set()
    try:
        iterator = raw_channels if isinstance(raw_channels, (list, tuple, set)) else []
        for raw_channel in iterator:
            channels.add(int(raw_channel))
    except (TypeError, ValueError):
        return []
    return sorted(channels)


def _stored_channel_names(raw_names: object, channels: list[int]) -> dict[int, str]:
    """Align the stored channel-name list with the channel list."""
    names: dict[int, str] = {}
    if not isinstance(raw_names, (list, tuple)):
        return names
    for channel, name in zip(channels, raw_names, strict=False):
        text = str(name).strip()
        if text:
            names[channel] = text
    return names


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hikvision Away Arm from a config entry."""
    data = entry.data
    options = entry.options

    channels = _normalise_channels(options.get(CONF_CHANNELS, data.get(CONF_CHANNELS)))
    if not channels:
        raise ConfigEntryNotReady("No NVR channels are configured for this entry.")

    client = HikvisionIsapiClient(
        data[CONF_HOST],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        port=int(data.get(CONF_PORT, DEFAULT_PORT)),
        use_ssl=bool(data.get(CONF_USE_SSL, DEFAULT_USE_SSL)),
        verify_ssl=bool(data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
    )

    try:
        device = await hass.async_add_executor_job(client.get_device_info)
    except HikvisionAuthError as err:
        await hass.async_add_executor_job(client.close)
        raise ConfigEntryAuthFailed(
            "The Hikvision NVR rejected the credentials."
        ) from err
    except HikvisionError as err:
        await hass.async_add_executor_job(client.close)
        raise ConfigEntryNotReady(
            f"Could not connect to the Hikvision NVR: {err}"
        ) from err

    names_by_id = _stored_channel_names(
        options.get(CONF_CHANNEL_NAMES, data.get(CONF_CHANNEL_NAMES)),
        channels,
    )
    try:
        nvr_channels = await hass.async_add_executor_job(client.get_channels)
    except HikvisionError as err:
        # The channel list is only used for friendly names.  Never fail setup
        # just because that optional request failed.
        _LOGGER.debug("Could not read NVR channel list: %s", err)
    else:
        nvr_names = {item.id: item.name for item in nvr_channels}
        for channel in channels:
            if channel in nvr_names:
                names_by_id[channel] = nvr_names[channel]

    channel_names = tuple(
        names_by_id.get(channel, f"Channel {channel}") for channel in channels
    )
    name = str(options.get(CONF_NAME) or data.get(CONF_NAME) or DEFAULT_NAME)
    scan_interval = int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    coordinator = HikvisionAwayCoordinator(hass, entry, client, channels, scan_interval)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await hass.async_add_executor_job(client.close)
        raise

    entry.runtime_data = HikvisionAwayRuntimeData(
        client=client,
        coordinator=coordinator,
        device=device,
        channels=tuple(channels),
        channel_names=channel_names,
        name=name,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime: HikvisionAwayRuntimeData = entry.runtime_data
        await hass.async_add_executor_job(runtime.client.close)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after options were changed."""
    await hass.config_entries.async_reload(entry.entry_id)
