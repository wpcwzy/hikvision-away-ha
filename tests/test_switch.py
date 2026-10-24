"""Tests for the switch entity and setup of the integration."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
from custom_components.hikvision_away.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.fakes import FakeClient


def make_entry() -> MockConfigEntry:
    """Return a config entry controlling channels 7 and 8."""
    return MockConfigEntry(
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


async def test_switch_controls_both_channels(hass: HomeAssistant) -> None:
    """One switch arms and disarms every selected NVR channel."""
    FakeClient.reset()
    entry = make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.hikvision_away.HikvisionIsapiClient",
        FakeClient,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    fake = FakeClient.latest()
    entity_ids = hass.states.async_entity_ids("switch")
    assert len(entity_ids) == 1
    entity_id = entity_ids[0]

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["channels"] == [7, 8]
    assert state.attributes["channel_names"] == ["餐厅", "客厅"]
    assert state.attributes["channel_states"] == {"7": False, "8": False}
    assert state.attributes["upload_center"] is False

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert fake.upload_center[7] is True
    assert fake.upload_center[8] is True
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes["channel_states"] == {"7": True, "8": True}

    # Simulate a partial state behind Home Assistant's back.
    fake.upload_center[8] = False
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF
    assert state.attributes["channel_states"] == {"7": True, "8": False}

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert fake.upload_center[7] is False
    assert fake.upload_center[8] is False
    assert hass.states.get(entity_id).state == STATE_OFF

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diagnostics["channels"] == [
        {"id": 7, "name": "餐厅"},
        {"id": 8, "name": "客厅"},
    ]
    assert diagnostics["coordinator"]["channel_data"]["8"]["upload_center"] is False

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert fake.closed is True
    assert entry.state is ConfigEntryState.NOT_LOADED
