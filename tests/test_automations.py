"""Sharing the user's own automations and scripts: reading the files without leaking
secrets, the opt-in, when the list is sent, and that the readings never suffer."""

from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fairgrow import automations as own
from custom_components.fairgrow.const import (
    CONF_ADAPTER_ID,
    CONF_ENTITIES,
    CONF_SHARE_AUTOMATIONS,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
)

URL = "https://fg.example"
API = URL + "/wp-json/fg-ha/v1"

AUTOMATIONS_YAML = """\
- id: '1700000000001'
  alias: Kas water geven
  triggers:
    - trigger: numeric_state
      entity_id: sensor.kas_vocht
      below: 30
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.klep
    - action: notify.telegram
      data:
        message: !secret telegram_tekst
- id: fairgrow_12
  alias: Gemaakt door FairGrow
  triggers: []
  actions: []
- alias: Zonder id
  trigger:
    platform: time
    at: '07:00:00'
  action: []
"""

SCRIPTS_YAML = """\
klep_puls:
  alias: Klep 5 minuten open
  sequence:
    - action: switch.turn_on
      target:
        entity_id: switch.klep
    - delay: '00:05:00'
    - action: switch.turn_off
      target:
        entity_id: switch.klep
zonder_alias:
  sequence:
    - action: light.toggle
      target:
        entity_id: light.kas
"""


def _write(config_dir: str, automations: str | None = AUTOMATIONS_YAML, scripts: str | None = SCRIPTS_YAML) -> None:
    for name, text in (("automations.yaml", automations), ("scripts.yaml", scripts)):
        path = Path(config_dir, name)
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- reading


def test_read_all_keeps_secrets_literal_and_maps_scripts(tmp_path: Path) -> None:
    """!secret stays text, FairGrow's own copies are marked, scripts get an id."""
    _write(str(tmp_path))
    out = own.read_all(str(tmp_path))
    assert out["bronnen"] == {"automations.yaml": "ok", "scripts.yaml": "ok"}
    assert out["totaal"] == 5 and out["afgekapt"] == 0
    by_id = {a["id"]: a for a in out["automations"]}
    kas = by_id["1700000000001"]
    assert kas["naam"] == "Kas water geven" and kas["soort"] == "automation" and not kas["van_ons"]
    assert kas["definitie"]["actions"][1]["data"]["message"] == "!secret telegram_tekst"
    assert by_id["fairgrow_12"]["van_ons"] is True
    assert by_id["zonder-id-3"]["naam"] == "Zonder id"
    puls = by_id["script.klep_puls"]
    assert puls["soort"] == "script" and puls["naam"] == "Klep 5 minuten open"
    assert puls["definitie"]["id"] == "klep_puls" and puls["definitie"]["sequence"][1]["delay"] == "00:05:00"
    assert by_id["script.zonder_alias"]["definitie"]["alias"] == "zonder_alias"
    json.dumps(out)  # everything JSON-safe


def test_read_all_reports_missing_empty_and_broken_files(tmp_path: Path) -> None:
    """A missing or broken file is reported, never raised."""
    _write(str(tmp_path), automations="- id: [unclosed\n", scripts=None)
    out = own.read_all(str(tmp_path))
    assert out["bronnen"]["automations.yaml"].startswith("error:")
    assert out["bronnen"]["scripts.yaml"] == "missing"
    assert out["automations"] == []
    _write(str(tmp_path), automations="", scripts="")
    out = own.read_all(str(tmp_path))
    assert out["bronnen"] == {"automations.yaml": "empty", "scripts.yaml": "empty"}


def test_include_tags_are_not_followed(tmp_path: Path) -> None:
    """!include is kept as text: no other file is read or sent."""
    Path(tmp_path, "geheim.yaml").write_text("wachtwoord: hunter2\n", encoding="utf-8")
    _write(str(tmp_path), automations="- id: a\n  alias: x\n  actions: !include geheim.yaml\n", scripts=None)
    out = own.read_all(str(tmp_path))
    assert out["automations"][0]["definitie"]["actions"] == "!include geheim.yaml"
    assert "hunter2" not in json.dumps(out)


def test_fingerprint_follows_the_files(tmp_path: Path) -> None:
    """Changes when a file changes; stable otherwise."""
    _write(str(tmp_path))
    a = own.fingerprint(str(tmp_path))
    assert a == own.fingerprint(str(tmp_path))
    Path(tmp_path, "scripts.yaml").write_text(SCRIPTS_YAML + "\nnieuw:\n  sequence: []\n", encoding="utf-8")
    os.utime(Path(tmp_path, "scripts.yaml"), ns=(1, 1))
    assert own.fingerprint(str(tmp_path)) != a


# ---------------------------------------------------------------- the pusher


def _entry(share: bool) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: URL, CONF_TOKEN: "t" * 48, CONF_ADAPTER_ID: 7},
        options={CONF_ENTITIES: ["sensor.kas_temp"], CONF_SHARE_AUTOMATIONS: share},
    )


def _mock(aioclient_mock: AiohttpClientMocker, states: dict | None = None, automations: dict | None = None, **aut_kw) -> None:
    aioclient_mock.post(API + "/entities", json={"ok": True, "aangemeld": 1, "entiteiten": [], "push_seconden": 60})
    aioclient_mock.post(API + "/states", json=states or {"ok": True, "push_seconden": 60})
    aioclient_mock.post(API + "/heartbeat", json={"ok": True})
    aioclient_mock.post(API + "/automations", json=automations or {"ok": True, "opgeslagen": 5}, **aut_kw)


def _calls(aioclient_mock: AiohttpClientMocker, path: str) -> list:
    return [c for c in aioclient_mock.mock_calls if str(c[1]).endswith(path)]


async def test_off_by_default_sends_nothing(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Without the tick nothing about automations leaves Home Assistant."""
    _write(hass.config.config_dir)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: URL, CONF_TOKEN: "t" * 48, CONF_ADAPTER_ID: 7},
                            options={CONF_ENTITIES: ["sensor.kas_temp"]})
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.kas_temp", "20", {"device_class": "temperature"})
    _mock(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not _calls(aioclient_mock, "/automations")
    hb = _calls(aioclient_mock, "/heartbeat")[0][2]
    assert hb["automations_delen"] is False and hb["versie"] == "0.2.0"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_sent_on_start_when_switched_on(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Opted in: the list goes up at start, with the token and the version."""
    _write(hass.config.config_dir)
    entry = _entry(True)
    entry.add_to_hass(hass)
    _mock(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    call = _calls(aioclient_mock, "/automations")[0]
    assert call[3]["X-Adapter-Token"] == "t" * 48
    body = call[2]
    assert body["versie"] == "0.2.0" and body["totaal"] == 5
    assert {a["id"] for a in body["automations"]} >= {"1700000000001", "script.klep_puls"}
    stats = entry.runtime_data.stats
    assert stats["automations_count"] == 5 and stats["automations_error"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_sent_again_on_change_and_on_request_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """Unchanged files: quiet. Changed file: sent. FairGrow asks: sent."""
    _write(hass.config.config_dir)
    entry = _entry(True)
    entry.add_to_hass(hass)
    _mock(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(_calls(aioclient_mock, "/automations")) == 1

    freezer.tick(timedelta(seconds=75))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(_calls(aioclient_mock, "/automations")) == 1  # nothing changed

    Path(hass.config.config_dir, "scripts.yaml").write_text(SCRIPTS_YAML + "nieuw:\n  sequence: []\n", encoding="utf-8")
    os.utime(Path(hass.config.config_dir, "scripts.yaml"), ns=(10**18, 10**18))
    freezer.tick(timedelta(seconds=75))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    calls = _calls(aioclient_mock, "/automations")
    assert len(calls) == 2 and calls[-1][2]["totaal"] == 6

    # FairGrow asks for the list in its answer to the readings.
    aioclient_mock.clear_requests()
    _mock(aioclient_mock, states={"ok": True, "push_seconden": 60, "automations_sturen": True})
    hass.states.async_set("sensor.kas_temp", "23", {"device_class": "temperature"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=65))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(_calls(aioclient_mock, "/automations")) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_request_arrives_by_heartbeat_without_readings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """No new readings: the heartbeat (every minute while sharing) carries the request."""
    _write(hass.config.config_dir)
    entry = _entry(True)
    entry.add_to_hass(hass)
    _mock(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    aioclient_mock.post(API + "/states", json={"ok": True, "push_seconden": 60})
    aioclient_mock.post(API + "/heartbeat", json={"ok": True, "automations_sturen": True})
    aioclient_mock.post(API + "/automations", json={"ok": True})
    for _ in range(8):  # 80 seconds, no state changes at all
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert _calls(aioclient_mock, "/heartbeat")
    assert not _calls(aioclient_mock, "/states")
    assert len(_calls(aioclient_mock, "/automations")) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_older_fairgrow_and_errors_do_not_hurt_readings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A FairGrow without the route answers 404: noted, readings still flow."""
    _write(hass.config.config_dir)
    entry = _entry(True)
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.kas_temp", "20", {"device_class": "temperature"})
    _mock(aioclient_mock, automations={"code": "rest_no_route", "message": "No route"}, status=404)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    pusher = entry.runtime_data
    assert pusher.stats["automations_error"] == "FairGrow does not accept automations yet"
    assert _calls(aioclient_mock, "/states") and len(pusher.queue) == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_broken_yaml_is_reported_not_raised(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A broken automations.yaml still sends the scripts and says what is wrong."""
    _write(hass.config.config_dir, automations="- id: [kapot\n")
    entry = _entry(True)
    entry.add_to_hass(hass)
    _mock(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    body = _calls(aioclient_mock, "/automations")[0][2]
    assert body["bronnen"]["automations.yaml"].startswith("error:")
    assert {a["soort"] for a in body["automations"]} == {"script"}
    assert await hass.config_entries.async_unload(entry.entry_id)
