"""The pusher: entity announcement, batching, queue on network loss, auth loss,
diagnostics. The FairGrow HTTP API is mocked with aioclient_mock."""

from __future__ import annotations

from datetime import timedelta
import json

from aiohttp import ClientError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fairgrow.const import (
    CONF_ADAPTER_ID,
    CONF_ENTITIES,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
)
from custom_components.fairgrow.diagnostics import async_get_config_entry_diagnostics

URL = "https://fg.example"
API = URL + "/wp-json/fg-ha/v1"
TOKEN = "geheim-token-dat-nooit-mag-lekken-000000000000000"


def _entry(entities: list[str]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home Assistant Thuis",
        data={CONF_URL: URL, CONF_TOKEN: TOKEN, CONF_ADAPTER_ID: 7},
        options={CONF_ENTITIES: entities},
    )


def _mock_ok(aioclient_mock: AiohttpClientMocker, **states_extra) -> None:
    aioclient_mock.post(API + "/entities", json={"ok": True, "aangemeld": 2, "entiteiten": [
        {"entity_id": "sensor.kas_temp", "status": "ok"},
        {"entity_id": "binary_sensor.deur", "status": "ok"},
        {"entity_id": "sensor.raar", "status": "ignored", "reden": "unsupported"}], "push_seconden": 60})
    aioclient_mock.post(API + "/states", json={"ok": True, "verwerkt": 1, "push_seconden": 60, **states_extra})
    aioclient_mock.post(API + "/heartbeat", json={"ok": True})


def _calls(aioclient_mock: AiohttpClientMocker, path: str) -> list:
    return [c for c in aioclient_mock.mock_calls if str(c[1]).endswith(path)]


async def _setup_registry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A ZHA-like device in the area Kas with a temperature entity."""
    other = MockConfigEntry(domain="zha")
    other.add_to_hass(hass)
    area = ar.async_get(hass).async_create("Kas")
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id,
        identifiers={("zha", "00:15:8d:00:01:02:03:04")},
        connections={("zigbee", "00:15:8d:00:01:02:03:04")},
        manufacturer="LUMI", model="lumi.weather", name="Kas sensor",
    )
    dr.async_get(hass).async_update_device(dev.id, area_id=area.id)
    er.async_get(hass).async_get_or_create(
        "sensor", "zha", "kas-temp", suggested_object_id="kas_temp", device_id=dev.id,
        original_device_class="temperature", unit_of_measurement="°C",
    )


async def test_setup_announces_and_pushes_current_states(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """On start: entity list with device and area, then the current readings."""
    entry = _entry(["sensor.kas_temp", "binary_sensor.deur"])
    entry.add_to_hass(hass)
    await _setup_registry(hass, entry)
    hass.states.async_set("sensor.kas_temp", "21.5", {"device_class": "temperature", "unit_of_measurement": "°C", "friendly_name": "Kas temperatuur"})
    hass.states.async_set("binary_sensor.deur", "off", {"device_class": "door"})
    hass.states.async_set("sensor.niet_gedeeld", "5", {"device_class": "temperature"})
    _mock_ok(aioclient_mock)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED

    ent_call = _calls(aioclient_mock, "/entities")[0]
    assert ent_call[3]["X-Adapter-Token"] == TOKEN
    ents = {e["entity_id"]: e for e in ent_call[2]["entiteiten"]}
    assert set(ents) == {"sensor.kas_temp", "binary_sensor.deur"}
    kas = ents["sensor.kas_temp"]
    assert kas["device_class"] == "temperature" and kas["eenheid"] == "°C"
    assert kas["toestel"]["naam"] == "Kas sensor"
    assert kas["toestel"]["gebied"] == "Kas"
    assert kas["toestel"]["ieee"] == "0x00158d0001020304"
    assert "toestel" not in ents["binary_sensor.deur"]

    sent = _calls(aioclient_mock, "/states")[0][2]["states"]
    assert {(s["entity_id"], s["state"]) for s in sent} == {("sensor.kas_temp", "21.5"), ("binary_sensor.deur", "off")}
    assert all(isinstance(s["ts"], int) for s in sent)
    assert _calls(aioclient_mock, "/heartbeat")
    pusher = entry.runtime_data
    assert pusher.stats["ignored_entities"] == {"sensor.raar": "unsupported"}
    assert len(pusher.queue) == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_changes_are_batched_and_attribute_updates_skipped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """Changes of shared entities are queued and pushed on the tick; others never."""
    entry = _entry(["sensor.kas_temp"])
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.kas_temp", "20", {"device_class": "temperature"})
    _mock_ok(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    aioclient_mock.mock_calls.clear()

    hass.states.async_set("sensor.kas_temp", "21", {"device_class": "temperature"})
    hass.states.async_set("sensor.kas_temp", "21", {"device_class": "temperature", "x": 1})  # attribute only
    hass.states.async_set("sensor.kas_temp", "22", {"device_class": "temperature"})
    hass.states.async_set("sensor.ander", "99", {"device_class": "temperature"})
    await hass.async_block_till_done(wait_background_tasks=True)
    pusher = entry.runtime_data
    assert [q["state"] for q in pusher.queue] == ["21", "22"]

    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not _calls(aioclient_mock, "/states")  # not due yet
    freezer.tick(timedelta(seconds=45))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    sent = _calls(aioclient_mock, "/states")[0][2]["states"]
    assert [s["state"] for s in sent] == ["21", "22"]
    assert len(pusher.queue) == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_queue_survives_network_loss(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """FairGrow unreachable: nothing is lost, it is sent when it comes back."""
    entry = _entry(["sensor.kas_temp"])
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.kas_temp", "20", {"device_class": "temperature"})
    aioclient_mock.post(API + "/entities", exc=ClientError())
    assert await hass.config_entries.async_setup(entry.entry_id)  # starts anyway
    await hass.async_block_till_done(wait_background_tasks=True)
    pusher = entry.runtime_data
    for v in ("21", "22", "23"):
        hass.states.async_set("sensor.kas_temp", v, {"device_class": "temperature"})
    await hass.async_block_till_done(wait_background_tasks=True)
    await pusher.async_flush(force=True)
    assert [q["state"] for q in pusher.queue] == ["20", "21", "22", "23"]
    assert pusher.stats["failed_pushes"] >= 1
    assert pusher._backoff >= 30

    aioclient_mock.clear_requests()
    _mock_ok(aioclient_mock)
    await pusher.async_flush(force=True)
    sent = _calls(aioclient_mock, "/states")[0][2]["states"]
    assert [s["state"] for s in sent] == ["20", "21", "22", "23"]
    assert len(pusher.queue) == 0
    assert pusher._backoff == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_queue_is_saved_over_a_restart(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_storage: dict
) -> None:
    """Unload with an unsent queue -> stored -> sent after the next start."""
    entry = _entry(["sensor.kas_temp"])
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.kas_temp", "20", {"device_class": "temperature"})
    aioclient_mock.post(API + "/entities", exc=ClientError())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.states.async_set("sensor.kas_temp", "19", {"device_class": "temperature"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.queue"]["data"]["queue"]
    assert [q["state"] for q in stored] == ["20", "19"]

    aioclient_mock.clear_requests()
    _mock_ok(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    sent = _calls(aioclient_mock, "/states")[0][2]["states"]
    assert [s["state"] for s in sent] == ["20", "19", "19"]  # stored queue, then current state
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_revoked_token_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Disconnected on fairgrow.eu: setup fails with auth, a reauth flow appears."""
    entry = _entry(["sensor.kas_temp"])
    entry.add_to_hass(hass)
    aioclient_mock.post(API + "/entities", status=401, json={"code": "fg_ha_geen_token", "message": "revoked"})
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_diagnostics_do_not_leak_the_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The token appears nowhere in the diagnostics."""
    entry = _entry(["sensor.kas_temp"])
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.kas_temp", "20", {"device_class": "temperature"})
    _mock_ok(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag, default=str)
    assert TOKEN not in text
    assert diag["entry"]["data"][CONF_TOKEN] == "**REDACTED**"
    assert diag["pusher"]["entities"] == ["sensor.kas_temp"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_remove_entry_unpairs(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Deleting the integration revokes it on FairGrow."""
    entry = _entry([])
    entry.add_to_hass(hass)
    _mock_ok(aioclient_mock)
    aioclient_mock.post(API + "/unpair", json={"ok": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls(aioclient_mock, "/unpair")
