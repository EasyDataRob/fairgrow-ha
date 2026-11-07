"""Letting FairGrow switch lights and switches: only what is on the control list, only
on/off/toggle, never late, and the result goes back to FairGrow."""

from __future__ import annotations

from datetime import timedelta
import time

from freezegun.api import FrozenDateTimeFactory
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fairgrow import commands as own
from custom_components.fairgrow.config_flow import share_options
from custom_components.fairgrow.const import (
    CONF_ADAPTER_ID,
    CONF_CONTROL,
    CONF_ENTITIES,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
)

URL = "https://fg.example"
API = URL + "/wp-json/fg-ha/v1"
ALLOWED = ["light.lightstrip", "switch.pomp"]


# ---------------------------------------------------------------- the rules


def test_allowed_command_passes_and_domain_comes_from_the_entity() -> None:
    """switch.turn_on for a light becomes light.turn_on; light data is filtered."""
    now = time.time()
    dom, svc, eid, data = own.check(
        {"entity_id": "light.lightstrip", "dienst": "switch.turn_on", "gemaakt": now,
         "data": {"brightness_pct": 40, "entity_id": "lock.voordeur", "code": "1234"}},
        ALLOWED, now)
    assert (dom, svc, eid) == ("light", "turn_on", "light.lightstrip")
    assert data == {"brightness_pct": 40}
    _, svc, _, data = own.check({"entity_id": "switch.pomp", "dienst": "toggle", "gemaakt": now,
                                 "data": {"brightness": 9}}, ALLOWED, now)
    assert svc == "toggle" and data == {}


@pytest.mark.parametrize(
    ("command", "fragment"),
    [
        ({"entity_id": "light.keuken", "dienst": "light.turn_on"}, "not on the list"),
        ({"entity_id": "lock.voordeur", "dienst": "lock.unlock"}, "not on the list"),
        ({"entity_id": "light.lightstrip", "dienst": "light.set_color"}, "only turn on"),
        ({"entity_id": "switch.pomp", "dienst": "homeassistant.restart"}, "only turn on"),
        ({"entity_id": "switch.pomp", "dienst": "turn_on", "gemaakt": 0}, "too old"),
        ({"entity_id": "switch.pomp", "dienst": "turn_on", "gemaakt": "nu"}, "too old"),
    ],
)
def test_refused(command: dict, fragment: str) -> None:
    """Anything outside the list, the services or the time window is refused."""
    command.setdefault("gemaakt", time.time())
    with pytest.raises(own.Refused, match=fragment):
        own.check(command, ALLOWED, time.time())


def test_only_lights_and_switches_even_if_listed() -> None:
    """A lock that somehow ended up on the list still cannot be switched."""
    with pytest.raises(own.Refused, match="only switches lights"):
        own.check({"entity_id": "lock.voordeur", "dienst": "turn_on", "gemaakt": time.time()},
                  ["lock.voordeur"], time.time())


def test_control_list_is_shared_and_limited_to_lights_and_switches() -> None:
    """What FairGrow may switch is always shared; other domains never get on the list."""
    out = share_options({CONF_ENTITIES: ["sensor.kas_temp"],
                         CONF_CONTROL: ["light.lightstrip", "lock.voordeur"]})
    assert out[CONF_CONTROL] == ["light.lightstrip"]
    assert out[CONF_ENTITIES] == ["sensor.kas_temp", "light.lightstrip"]


# ---------------------------------------------------------------- the pusher


def _entry(control: list[str]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: URL, CONF_TOKEN: "t" * 48, CONF_ADAPTER_ID: 7},
        options={CONF_ENTITIES: ["sensor.kas_temp", *control], CONF_CONTROL: control},
    )


def _mock(aioclient_mock: AiohttpClientMocker, commands: dict | None = None, **kw) -> None:
    aioclient_mock.post(API + "/entities", json={"ok": True, "aangemeld": 2, "entiteiten": []})
    aioclient_mock.post(API + "/states", json={"ok": True})
    aioclient_mock.post(API + "/heartbeat", json={"ok": True})
    aioclient_mock.post(API + "/commands", json=commands or {"ok": True, "opdrachten": []}, **kw)


def _calls(aioclient_mock: AiohttpClientMocker, path: str) -> list:
    return [c for c in aioclient_mock.mock_calls if str(c[1]).endswith(path)]


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int = 10) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_no_control_list_no_polling(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """Without anything to switch the integration never asks for commands."""
    entry = _entry([])
    entry.add_to_hass(hass)
    _mock(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert not _calls(aioclient_mock, "/commands")
    assert _calls(aioclient_mock, "/entities")[0][2]["entiteiten"][0]["bedienbaar"] is False
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_command_runs_and_result_goes_back(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """FairGrow asks for light on: the service runs, the result is reported."""
    hass.states.async_set("light.lightstrip", "off")
    calls_on = async_mock_service(hass, "light", "turn_on")
    calls_lock = async_mock_service(hass, "lock", "unlock")
    entry = _entry(["light.lightstrip"])
    entry.add_to_hass(hass)
    _mock(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ent = {e["entity_id"]: e for e in _calls(aioclient_mock, "/entities")[0][2]["entiteiten"]}
    assert ent["light.lightstrip"]["bedienbaar"] is True and ent["sensor.kas_temp"]["bedienbaar"] is False

    now = dt_util.utcnow().timestamp()
    aioclient_mock.clear_requests()
    _mock(aioclient_mock, commands={"ok": True, "opdrachten": [
        {"id": 11, "entity_id": "light.lightstrip", "dienst": "light.turn_on", "gemaakt": now + 5,
         "data": {"brightness_pct": 60}},
        {"id": 12, "entity_id": "lock.voordeur", "dienst": "lock.unlock", "gemaakt": now + 5},
    ]})
    await _tick(hass, freezer)
    assert len(calls_on) == 1
    assert calls_on[0].data["brightness_pct"] == 60
    assert calls_on[0].data["entity_id"] in ("light.lightstrip", ["light.lightstrip"])
    assert not calls_lock
    posts = _calls(aioclient_mock, "/commands")
    assert posts[0][2] == {"resultaten": []}
    results = {r["id"]: r for r in posts[1][2]["resultaten"]}
    assert results[11]["ok"] is True
    assert results[12]["ok"] is False and results[12]["geweigerd"] is True
    stats = entry.runtime_data.stats
    assert stats["commands_done"] == 1 and stats["commands_refused"] == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_results_wait_when_fairgrow_is_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """A failed round keeps the results for the next one; readings do not suffer."""
    async_mock_service(hass, "switch", "turn_off")
    entry = _entry(["switch.pomp"])
    entry.add_to_hass(hass)
    _mock(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    pusher = entry.runtime_data
    pusher._cmd_results = [{"id": 5, "ok": True}]
    aioclient_mock.clear_requests()
    _mock(aioclient_mock, commands={"message": "down"}, status=503)
    await _tick(hass, freezer)
    assert pusher._cmd_results == [{"id": 5, "ok": True}]
    assert pusher.stats["commands_error"]
    aioclient_mock.clear_requests()
    _mock(aioclient_mock)
    await _tick(hass, freezer)
    assert _calls(aioclient_mock, "/commands")[0][2] == {"resultaten": [{"id": 5, "ok": True}]}
    assert pusher._cmd_results == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_older_fairgrow_pauses_polling(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """A FairGrow without the route: ask again after an hour, not every ten seconds."""
    entry = _entry(["switch.pomp"])
    entry.add_to_hass(hass)
    _mock(aioclient_mock, commands={"code": "rest_no_route"}, status=404)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer)
    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert len(_calls(aioclient_mock, "/commands")) == 1
    assert entry.runtime_data.stats["commands_error"] == "FairGrow does not send commands yet"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_light_change_is_pushed_on_the_next_tick(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """A light that turns on reaches FairGrow within a tick, not after a minute."""
    hass.states.async_set("light.lightstrip", "off")
    entry = _entry(["light.lightstrip"])
    entry.add_to_hass(hass)
    _mock(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    _mock(aioclient_mock)
    hass.states.async_set("light.lightstrip", "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer)
    states = _calls(aioclient_mock, "/states")
    assert states and states[0][2]["states"][-1]["state"] == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)
