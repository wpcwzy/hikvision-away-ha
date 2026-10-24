"""Data coordinator for the Hikvision NVR Away Arm integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    HikvisionAuthError,
    HikvisionError,
    HikvisionIsapiClient,
    LinkageState,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChannelLinkage:
    """Linkage state of one NVR channel."""

    channel: int
    upload_center: bool
    notification_methods: tuple[str, ...]

    @classmethod
    def from_state(cls, channel: int, state: LinkageState) -> ChannelLinkage:
        return cls(
            channel=channel,
            upload_center=state.upload_center,
            notification_methods=state.notification_methods,
        )


@dataclass(frozen=True, slots=True)
class HikvisionAwayData:
    """State read from all channels controlled by one switch."""

    channels: tuple[ChannelLinkage, ...]

    @property
    def all_upload_center_enabled(self) -> bool:
        """Return true only when every selected channel is armed."""
        return bool(self.channels) and all(
            channel.upload_center for channel in self.channels
        )

    @property
    def channel_states(self) -> dict[str, bool]:
        """Return a JSON-friendly channel -> armed mapping."""
        return {
            str(channel.channel): channel.upload_center for channel in self.channels
        }

    @property
    def notification_methods(self) -> dict[str, list[str]]:
        """Return a JSON-friendly channel -> notification methods mapping."""
        return {
            str(channel.channel): list(channel.notification_methods)
            for channel in self.channels
        }


class HikvisionAwayCoordinator(DataUpdateCoordinator[HikvisionAwayData]):
    """Poll the NVR and perform serialised read/write API calls."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: HikvisionIsapiClient,
        channels: list[int],
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"hikvision_away_channels_{'-'.join(map(str, channels))}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.channels = tuple(sorted({int(channel) for channel in channels}))
        self._api_lock = asyncio.Lock()

    async def _async_update_data(self) -> HikvisionAwayData:
        try:
            return await self._async_read_state()
        except HikvisionAuthError as err:
            raise ConfigEntryAuthFailed(
                "Authentication with the Hikvision NVR failed."
            ) from err
        except HikvisionError as err:
            raise UpdateFailed(
                f"Could not read the field-detection linkage: {err}"
            ) from err

    async def _async_read_state(self) -> HikvisionAwayData:
        async with self._api_lock:
            states = [
                ChannelLinkage.from_state(
                    channel,
                    await self.hass.async_add_executor_job(
                        self.client.get_linkage_state, channel
                    ),
                )
                for channel in self.channels
            ]
        return HikvisionAwayData(tuple(states))

    async def async_set_upload_center(self, enabled: bool) -> None:
        """Enable/disable the upload-center link on all selected channels.

        All API calls are serialised because ``requests.Session`` is not a
        thread-safe object.  If one channel fails we still read back every
        channel so the switch shows the actual (possibly partial) NVR state,
        then raise an error for the UI.
        """
        write_error: HikvisionError | None = None
        async with self._api_lock:
            for channel in self.channels:
                try:
                    await self.hass.async_add_executor_job(
                        self.client.set_upload_center, channel, enabled
                    )
                except HikvisionAuthError as err:
                    # No point in writing the remaining channels with the
                    # same rejected credentials.
                    write_error = err
                    break
                except HikvisionError as err:
                    if write_error is None:
                        write_error = err

            states = [
                ChannelLinkage.from_state(
                    channel,
                    await self.hass.async_add_executor_job(
                        self.client.get_linkage_state, channel
                    ),
                )
                for channel in self.channels
            ]

        self.async_set_updated_data(HikvisionAwayData(tuple(states)))
        if write_error is not None:
            raise write_error
