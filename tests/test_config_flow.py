"""Config flow and options flow, with the platform mocked."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fairgrow.api import FairGrowCodeError, FairGrowError
from custom_components.fairgrow.const import (
    CONF_ADAPTER_ID,
    CONF_CODE,
    CONF_ENTITIES,
    CONF_SHARE_AUTOMATIONS,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
)

PAIRED = {"ok": True, "adapter_id": 41, "token": "t" * 48, "label": "Home Assistant Thuis"}


async def test_flow_pairs_and_chooses_entities(hass: HomeAssistant) -> None:
    """Code -> entities -> entry with token in data and choice in options."""
    hass.states.async_set("sensor.kas_temp", "21.5", {"device_class": "temperature", "unit_of_measurement": "°C"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    with patch("custom_components.fairgrow.config_flow.FairGrowApi.pair", return_value=PAIRED) as pair, \
         patch("custom_components.fairgrow.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: " 7k4m-2qp9 "})
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "entities"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ENTITIES: ["sensor.kas_temp"]})
    assert pair.call_args.args[0] == "7k4m-2qp9"
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home Assistant Thuis"
    assert result["data"] == {CONF_URL: DEFAULT_URL, CONF_TOKEN: "t" * 48, CONF_ADAPTER_ID: 41}
    # Sharing automations is never on unless the user ticks it.
    assert result["options"] == {CONF_ENTITIES: ["sensor.kas_temp"], CONF_SHARE_AUTOMATIONS: False}


async def test_flow_code_errors(hass: HomeAssistant) -> None:
    """Every refusal of the platform lands on the code field with its own text."""
    for reason in ("invalid_code", "code_used", "code_expired", "bridge_code", "too_many_attempts"):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        with patch("custom_components.fairgrow.config_flow.FairGrowApi.pair", side_effect=FairGrowCodeError(reason)):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: "AAAA-BBBB"})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_CODE: reason}
        hass.config_entries.flow.async_abort(result["flow_id"])


async def test_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Network trouble is a base error, and the form stays."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch("custom_components.fairgrow.config_flow.FairGrowApi.pair", side_effect=FairGrowError("down")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: "AAAA-BBBB"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_flow_changes_entities(hass: HomeAssistant) -> None:
    """Configure -> new choice is stored in the options."""
    hass.states.async_set("sensor.a", "1", {"device_class": "temperature"})
    hass.states.async_set("binary_sensor.deur", "off", {"device_class": "door"})
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: DEFAULT_URL, CONF_TOKEN: "x", CONF_ADAPTER_ID: 1},
                            options={CONF_ENTITIES: ["sensor.a"]})
    entry.add_to_hass(hass)
    with patch("custom_components.fairgrow.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_ENTITIES: ["sensor.a", "binary_sensor.deur"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_ENTITIES: ["sensor.a", "binary_sensor.deur"], CONF_SHARE_AUTOMATIONS: False}


async def test_options_flow_switches_on_automations_and_keeps_it(hass: HomeAssistant) -> None:
    """Ticking the box is stored, and the next form shows it ticked."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: DEFAULT_URL, CONF_TOKEN: "x", CONF_ADAPTER_ID: 1},
                            options={CONF_ENTITIES: ["sensor.a"]})
    entry.add_to_hass(hass)
    with patch("custom_components.fairgrow.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_ENTITIES: ["sensor.a"], CONF_SHARE_AUTOMATIONS: True})
        assert entry.options == {CONF_ENTITIES: ["sensor.a"], CONF_SHARE_AUTOMATIONS: True}
        result = await hass.config_entries.options.async_init(entry.entry_id)
        schema = result["data_schema"].schema
        default = next(k.default() for k in schema if k == CONF_SHARE_AUTOMATIONS)
        assert default is True


async def test_reauth_replaces_token(hass: HomeAssistant) -> None:
    """A refused token leads to a new pairing code; the entity choice stays."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: DEFAULT_URL, CONF_TOKEN: "oud", CONF_ADAPTER_ID: 1},
                            options={CONF_ENTITIES: ["sensor.a"]})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch("custom_components.fairgrow.config_flow.FairGrowApi.pair", return_value=PAIRED), \
         patch("custom_components.fairgrow.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: "AAAA-BBBB"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "t" * 48
    assert entry.options == {CONF_ENTITIES: ["sensor.a"]}
