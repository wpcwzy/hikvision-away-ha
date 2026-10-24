"""Tests for the Hikvision Away Arm config and options flows."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_away.api import HikvisionAuthError
from custom_components.hikvision_away.const import (
    CONF_CHANNEL_NAMES,
    CONF_CHANNELS,
    CONF_DEVICE_SERIAL,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from tests.fakes import FakeClient

USER_INPUT = {
    CONF_HOST: "192.0.2.1",
    CONF_PORT: 80,
    CONF_USE_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
}


async def test_user_flow_creates_multi_channel_entry(hass: HomeAssistant) -> None:
    """The flow creates one switch entry controlling channels 7 and 8."""
    fake = FakeClient()
    with patch(
        "custom_components.hikvision_away.config_flow._client_from_data",
        return_value=fake,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "channels"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CHANNELS: ["7", "8"], CONF_NAME: "离家布防模式"},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY

    assert result["title"] == "离家布防模式"
    assert result["data"][CONF_CHANNELS] == [7, 8]
    assert result["data"][CONF_CHANNEL_NAMES] == ["餐厅", "客厅"]
    assert result["data"][CONF_DEVICE_SERIAL] == "TEST-SERIAL"
    assert result["options"][CONF_CHANNELS] == [7, 8]
    assert result["options"][CONF_SCAN_INTERVAL] == 30
    assert result["result"].unique_id == "TEST-SERIAL:7,8"


async def test_user_flow_rejects_bad_credentials(hass: HomeAssistant) -> None:
    """An authentication failure is shown on the first form."""
    fake = FakeClient()
    with (
        patch(
            "custom_components.hikvision_away.config_flow._client_from_data",
            return_value=fake,
        ),
        patch.object(
            fake,
            "get_device_info",
            side_effect=HikvisionAuthError("bad password"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_empty_channel_selection_is_rejected(hass: HomeAssistant) -> None:
    """At least one channel must be selected."""
    fake = FakeClient()
    with patch(
        "custom_components.hikvision_away.config_flow._client_from_data",
        return_value=fake,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CHANNELS: [], CONF_NAME: "离家布防模式"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "channels"
    assert result["errors"] == {"base": "invalid_channel"}


async def test_duplicate_channel_set_is_rejected(hass: HomeAssistant) -> None:
    """Adding the same serial+channels twice aborts cleanly."""
    fake = FakeClient()
    with patch(
        "custom_components.hikvision_away.config_flow._client_from_data",
        return_value=fake,
    ):
        first = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        first = await hass.config_entries.flow.async_configure(
            first["flow_id"], USER_INPUT
        )
        first = await hass.config_entries.flow.async_configure(
            first["flow_id"], {CONF_CHANNELS: ["7", "8"], CONF_NAME: "离家布防模式"}
        )
        assert first["type"] is FlowResultType.CREATE_ENTRY

        second = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        second = await hass.config_entries.flow.async_configure(
            second["flow_id"], USER_INPUT
        )
        second = await hass.config_entries.flow.async_configure(
            second["flow_id"], {CONF_CHANNELS: ["8", "7"], CONF_NAME: "另一个名字"}
        )

    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_configured"


async def test_options_flow_changes_channels_and_interval(
    hass: HomeAssistant,
) -> None:
    """Options can change the channels, name, and polling interval."""
    fake = FakeClient()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="离家布防模式",
        unique_id="TEST-SERIAL:7,8",
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 80,
            CONF_USE_SSL: False,
            CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_CHANNELS: [7, 8],
            CONF_CHANNEL_NAMES: ["餐厅", "客厅"],
            CONF_DEVICE_SERIAL: "TEST-SERIAL",
            CONF_NAME: "离家布防模式",
        },
        options={
            CONF_CHANNELS: [7, 8],
            CONF_CHANNEL_NAMES: ["餐厅", "客厅"],
            CONF_NAME: "离家布防模式",
            CONF_SCAN_INTERVAL: 30,
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.hikvision_away.config_flow._client_from_data",
        return_value=fake,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_CHANNELS: ["1", "2"],
                CONF_NAME: "前后院布防",
                CONF_SCAN_INTERVAL: 60,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CHANNELS] == [1, 2]
    assert entry.options[CONF_CHANNEL_NAMES] == ["前院", "后院"]
    assert entry.options[CONF_SCAN_INTERVAL] == 60
    assert entry.title == "前后院布防"
    assert entry.unique_id == "TEST-SERIAL:1,2"
