"""Config flow for the Hikvision NVR Away Arm integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    Channel,
    DeviceInfo,
    HikvisionAuthError,
    HikvisionConnectionError,
    HikvisionError,
    HikvisionIsapiClient,
    HikvisionResponseError,
)
from .const import (
    CONF_CHANNEL_NAMES,
    CONF_CHANNELS,
    CONF_DEVICE_SERIAL,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    PREFERRED_CHANNELS,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=65535,
                mode=NumberSelectorMode.BOX,
                step=1,
            )
        ),
        vol.Required(CONF_USE_SSL, default=False): BooleanSelector(),
        vol.Required(CONF_VERIFY_SSL, default=False): BooleanSelector(),
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _normalise_connection(user_input: Mapping[str, Any]) -> dict[str, Any]:
    host = str(user_input[CONF_HOST]).strip()
    use_ssl = bool(user_input.get(CONF_USE_SSL, False))
    port = int(float(user_input[CONF_PORT]))
    if "://" in host:
        # Be forgiving when a user pastes http://ip:port from a browser.
        parsed = urlsplit(host)
        if parsed.scheme:
            use_ssl = parsed.scheme.lower() == "https"
        if parsed.hostname:
            host = parsed.hostname
        if parsed.port:
            port = parsed.port
    return {
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_USE_SSL: use_ssl,
        CONF_VERIFY_SSL: bool(user_input.get(CONF_VERIFY_SSL, False)),
        CONF_USERNAME: str(user_input[CONF_USERNAME]).strip(),
        CONF_PASSWORD: str(user_input[CONF_PASSWORD]),
    }


def _client_from_data(data: Mapping[str, Any]) -> HikvisionIsapiClient:
    return HikvisionIsapiClient(
        str(data[CONF_HOST]),
        str(data[CONF_USERNAME]),
        str(data[CONF_PASSWORD]),
        port=int(data.get(CONF_PORT, DEFAULT_PORT)),
        use_ssl=bool(data.get(CONF_USE_SSL, False)),
        verify_ssl=bool(data.get(CONF_VERIFY_SSL, False)),
    )


def _scan_interval_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_SCAN_INTERVAL,
            max=MAX_SCAN_INTERVAL,
            mode=NumberSelectorMode.BOX,
            step=1,
            unit_of_measurement="s",
        )
    )


def _channel_options(channels: list[Channel]) -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=str(channel.id), label=f"{channel.id} - {channel.name}")
        for channel in channels
    ]


def _channels_schema(
    channels: list[Channel],
    *,
    default_channels: list[int],
    default_name: str,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_CHANNELS,
                default=[str(channel) for channel in default_channels],
            ): SelectSelector(
                SelectSelectorConfig(
                    options=_channel_options(channels),
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_NAME, default=default_name): TextSelector(),
        }
    )


def _parse_selected_channels(
    user_input: Mapping[str, Any],
    available_channels: set[int],
) -> tuple[list[int], str | None]:
    raw_channels = user_input.get(CONF_CHANNELS)
    if raw_channels is None:
        return [], "invalid_channel"
    if isinstance(raw_channels, (str, int)):
        raw_channels = [raw_channels]
    if not isinstance(raw_channels, (list, tuple, set)):
        return [], "invalid_channel"
    try:
        channels = sorted({int(float(channel)) for channel in raw_channels})
    except (TypeError, ValueError):
        return [], "invalid_channel"
    if not channels:
        return [], "invalid_channel"
    if not set(channels).issubset(available_channels):
        return [], "invalid_channel"
    return channels, None


def _channel_names_for(
    channels: list[int], known_names: Mapping[int, str]
) -> list[str]:
    return [known_names.get(channel, f"Channel {channel}") for channel in channels]


def _default_channel_selection(channels: list[Channel]) -> list[int]:
    available = {channel.id for channel in channels}
    preferred = [channel for channel in PREFERRED_CHANNELS if channel in available]
    return preferred or [channels[0].id]


class HikvisionAwayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration of one or more NVR channels."""

    VERSION = 1

    def __init__(self) -> None:
        self._connection: dict[str, Any] = {}
        self._device: DeviceInfo | None = None
        self._channels: list[Channel] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for NVR connection details and load its channels."""
        errors: dict[str, str] = {}

        if user_input is not None:
            connection = _normalise_connection(user_input)
            if not connection[CONF_HOST]:
                errors["base"] = "invalid_host"
            elif not connection[CONF_USERNAME]:
                errors["base"] = "invalid_auth"
            else:
                client = _client_from_data(connection)
                try:
                    device = await self.hass.async_add_executor_job(
                        client.get_device_info
                    )
                    channels = await self.hass.async_add_executor_job(
                        client.get_channels
                    )
                except HikvisionAuthError:
                    errors["base"] = "invalid_auth"
                except HikvisionConnectionError:
                    errors["base"] = "cannot_connect"
                except HikvisionResponseError as err:
                    _LOGGER.debug("ISAPI error while validating NVR: %s", err)
                    errors["base"] = "api_error"
                except HikvisionError as err:
                    _LOGGER.debug("Unexpected error while validating NVR: %s", err)
                    errors["base"] = "unknown"
                else:
                    if not channels:
                        errors["base"] = "no_channels"
                    else:
                        self._connection = connection
                        self._device = device
                        self._channels = channels
                        return await self.async_step_channels()
                finally:
                    await self.hass.async_add_executor_job(client.close)

        return self.async_show_form(
            step_id="user",
            data_schema=CONNECT_SCHEMA,
            errors=errors,
        )

    async def async_step_channels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which channels the away-arm switch should control."""
        if not self._connection or self._device is None or not self._channels:
            return await self.async_step_user()

        errors: dict[str, str] = {}
        available_ids = {channel.id for channel in self._channels}
        name_by_id = {channel.id: channel.name for channel in self._channels}
        default_channels = _default_channel_selection(self._channels)
        default_name = DEFAULT_NAME

        if user_input is not None:
            selected_channels, error = _parse_selected_channels(
                user_input, available_ids
            )
            name = str(user_input.get(CONF_NAME, "")).strip() or DEFAULT_NAME
            default_name = name
            if selected_channels:
                default_channels = selected_channels
            if error is not None:
                errors["base"] = error
            else:
                serial = (
                    self._device.serial_number
                    or f"{self._connection[CONF_HOST]}:{self._connection[CONF_PORT]}"
                )
                unique_id = f"{serial}:{','.join(map(str, selected_channels))}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                client = _client_from_data(self._connection)
                bad_channels: list[int] = []
                try:
                    for channel in selected_channels:
                        try:
                            await self.hass.async_add_executor_job(
                                client.get_linkage_state, channel
                            )
                        except HikvisionResponseError:
                            bad_channels.append(channel)
                except HikvisionAuthError:
                    errors["base"] = "invalid_auth"
                except HikvisionConnectionError:
                    errors["base"] = "cannot_connect"
                except HikvisionError as err:
                    _LOGGER.debug("Error while validating channels: %s", err)
                    errors["base"] = "unknown"
                finally:
                    await self.hass.async_add_executor_job(client.close)

                if bad_channels and not errors:
                    _LOGGER.debug(
                        "Channels without fielddetection trigger: %s", bad_channels
                    )
                    errors["base"] = "channel_not_supported"

                if not errors:
                    channel_names = _channel_names_for(selected_channels, name_by_id)
                    data = {
                        **self._connection,
                        CONF_CHANNELS: selected_channels,
                        CONF_CHANNEL_NAMES: channel_names,
                        CONF_DEVICE_SERIAL: serial,
                        CONF_NAME: name,
                    }
                    options = {
                        CONF_CHANNELS: selected_channels,
                        CONF_CHANNEL_NAMES: channel_names,
                        CONF_NAME: name,
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    }
                    return self.async_create_entry(
                        title=name, data=data, options=options
                    )

        return self.async_show_form(
            step_id="channels",
            data_schema=_channels_schema(
                self._channels,
                default_channels=default_channels,
                default_name=default_name,
            ),
            errors=errors,
            description_placeholders={
                "model": self._device.model,
                "host": str(self._connection.get(CONF_HOST, "")),
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start the re-authentication flow after an authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                **entry.data,
                CONF_USERNAME: str(user_input[CONF_USERNAME]).strip(),
                CONF_PASSWORD: str(user_input[CONF_PASSWORD]),
            }
            if not data[CONF_USERNAME]:
                errors["base"] = "invalid_auth"
            else:
                client = _client_from_data(data)
                try:
                    await self.hass.async_add_executor_job(client.get_device_info)
                except HikvisionAuthError:
                    errors["base"] = "invalid_auth"
                except HikvisionConnectionError:
                    errors["base"] = "cannot_connect"
                except HikvisionError as err:
                    _LOGGER.debug("Reauth validation failed: %s", err)
                    errors["base"] = "unknown"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_USERNAME: data[CONF_USERNAME],
                            CONF_PASSWORD: data[CONF_PASSWORD],
                        },
                    )
                finally:
                    await self.hass.async_add_executor_job(client.close)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=entry.data[CONF_USERNAME],
                    ): TextSelector(),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return HikvisionAwayOptionsFlow()


class HikvisionAwayOptionsFlow(OptionsFlow):
    """Allow changing the channels, entity name and polling interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        entry = self.hass.config_entries.async_get_entry(self.handler)
        if entry is None:
            return self.async_abort(reason="entry_not_found")

        current_channels = sorted(
            {
                int(channel)
                for channel in entry.options.get(
                    CONF_CHANNELS, entry.data.get(CONF_CHANNELS, [])
                )
            }
        )
        current_names = list(
            entry.options.get(
                CONF_CHANNEL_NAMES,
                entry.data.get(CONF_CHANNEL_NAMES, []),
            )
        )
        stored_names = {
            channel: str(name)
            for channel, name in zip(current_channels, current_names, strict=False)
        }
        current_name = (
            str(entry.options.get(CONF_NAME, entry.data.get(CONF_NAME, DEFAULT_NAME)))
            or DEFAULT_NAME
        )
        current_interval = int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )

        channels: list[Channel] = []
        client = _client_from_data(entry.data)
        try:
            channels = await self.hass.async_add_executor_job(client.get_channels)
        except HikvisionError as err:
            _LOGGER.debug("Could not refresh channels for options flow: %s", err)
        finally:
            await self.hass.async_add_executor_job(client.close)

        known_ids = {channel.id for channel in channels}
        for channel in current_channels:
            if channel not in known_ids:
                channels.append(
                    Channel(
                        id=channel, name=stored_names.get(channel, f"Channel {channel}")
                    )
                )
        channels.sort(key=lambda channel: channel.id)
        available_ids = {channel.id for channel in channels}
        name_by_id = {channel.id: channel.name for channel in channels}
        for channel in current_channels:
            name_by_id.setdefault(
                channel, stored_names.get(channel, f"Channel {channel}")
            )

        errors: dict[str, str] = {}
        form_channels = current_channels
        form_name = current_name
        form_interval = current_interval

        if user_input is not None:
            selected_channels, error = _parse_selected_channels(
                user_input, available_ids
            )
            if selected_channels:
                form_channels = selected_channels
            form_name = str(user_input.get(CONF_NAME, "")).strip() or DEFAULT_NAME
            try:
                form_interval = int(float(user_input[CONF_SCAN_INTERVAL]))
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_scan_interval"
            else:
                if not MIN_SCAN_INTERVAL <= form_interval <= MAX_SCAN_INTERVAL:
                    errors["base"] = "invalid_scan_interval"
            if error is not None:
                errors["base"] = error

            channel_names = _channel_names_for(form_channels, name_by_id)
            serial = str(
                entry.data.get(CONF_DEVICE_SERIAL)
                or f"{entry.data[CONF_HOST]}:{entry.data.get(CONF_PORT, DEFAULT_PORT)}"
            )
            unique_id = f"{serial}:{','.join(map(str, form_channels))}"
            for other in self.hass.config_entries.async_entries(DOMAIN):
                if other.entry_id != entry.entry_id and other.unique_id == unique_id:
                    errors["base"] = "already_configured"
                    break

            new_channels = [
                channel for channel in form_channels if channel not in current_channels
            ]
            if new_channels and not errors:
                validation_client = _client_from_data(entry.data)
                bad_channels: list[int] = []
                try:
                    for channel in new_channels:
                        try:
                            await self.hass.async_add_executor_job(
                                validation_client.get_linkage_state, channel
                            )
                        except HikvisionResponseError:
                            bad_channels.append(channel)
                except HikvisionAuthError:
                    errors["base"] = "invalid_auth"
                except HikvisionConnectionError:
                    errors["base"] = "cannot_connect"
                except HikvisionError:
                    errors["base"] = "unknown"
                finally:
                    await self.hass.async_add_executor_job(validation_client.close)
                if bad_channels and not errors:
                    errors["base"] = "channel_not_supported"

            if not errors:
                new_options = {
                    CONF_CHANNELS: form_channels,
                    CONF_CHANNEL_NAMES: channel_names,
                    CONF_NAME: form_name,
                    CONF_SCAN_INTERVAL: form_interval,
                }
                # Update all changed entry fields in one call so the update
                # listener reloads the entry only once.  The options flow
                # manager will persist the same options again on completion,
                # which is a no-op because they already match.
                self.hass.config_entries.async_update_entry(
                    entry,
                    unique_id=(
                        unique_id if unique_id != entry.unique_id else entry.unique_id
                    ),
                    title=form_name,
                    options=new_options,
                )
                return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CHANNELS,
                        default=[str(channel) for channel in form_channels],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_channel_options(channels),
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_NAME, default=form_name): TextSelector(),
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=form_interval
                    ): _scan_interval_selector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "host": str(entry.data[CONF_HOST]),
                "model": "Hikvision NVR",
            },
        )
