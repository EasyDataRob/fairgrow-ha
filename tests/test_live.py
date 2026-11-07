"""End to end against the REAL fairgrow.eu (run with --live).

Needs two FairGrow accounts in the environment (application passwords, never in
this repository):
    FG_LIVE_URL        default https://fairgrow.eu
    FG_LIVE_A_USER     FG_LIVE_A_PW    account that makes the pairing code
    FG_LIVE_B_USER     FG_LIVE_B_PW    a second, unrelated account

The Home Assistant here is the test instance of pytest-homeassistant-custom-component
(an in-process `hass`, no frontend, no real ZHA), but the integration code, the
config flow, the HTTP calls and the platform are all real.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from unittest.mock import patch

import aiohttp

import pytest
import pytest_socket
import requests
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.fairgrow.const import CONF_CODE, CONF_ENTITIES, CONF_URL, DOMAIN

pytestmark = pytest.mark.live

URL = os.environ.get("FG_LIVE_URL", "https://fairgrow.eu").rstrip("/")


@pytest.fixture
async def live_session(live_network: None):
    """A plain aiohttp session for the integration.

    The shared session of the test harness carries a DNS resolver bound to another
    event loop ("attached to a different loop"), so for the live test the
    integration gets a normal session with the threaded resolver. In a real Home
    Assistant async_get_clientsession() is used unchanged."""
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver()))
    with patch("custom_components.fairgrow.config_flow.async_get_clientsession", return_value=session),          patch("custom_components.fairgrow.coordinator.async_get_clientsession", return_value=session),          patch("custom_components.fairgrow.async_get_clientsession", return_value=session):
        yield session
    await session.close()


@pytest.fixture
def live_network(socket_enabled: None):
    """Undo the network block of the test harness for this test only."""
    from pytest_homeassistant_custom_component import plugins

    pytest_socket._remove_restrictions()  # noqa: SLF001
    saved = socket.getaddrinfo
    socket.getaddrinfo = plugins._real_getaddrinfo  # noqa: SLF001
    yield
    socket.getaddrinfo = saved


def _session(user_env: str, pw_env: str) -> requests.Session:
    user, pw = os.environ.get(user_env), os.environ.get(pw_env)
    if not user or not pw:
        pytest.skip(f"{user_env}/{pw_env} not set")
    s = requests.Session()
    s.auth = (user, pw)
    s.headers["User-Agent"] = "fairgrow-ha-livetest"
    return s


async def test_live_pair_push_and_isolation(hass: HomeAssistant, live_session: aiohttp.ClientSession) -> None:
    """Pair with a real code, push a real reading, and check who can see it."""
    a = _session("FG_LIVE_A_USER", "FG_LIVE_A_PW")
    b = _session("FG_LIVE_B_USER", "FG_LIVE_B_PW")
    api = URL + "/wp-json"

    def get(s: requests.Session, path: str) -> requests.Response:
        return s.get(api + path, timeout=30)

    # 1. account A makes a pairing code on fairgrow.eu
    r = await hass.async_add_executor_job(lambda: a.post(api + "/fg-ha/v1/pair/new", json={}, timeout=30))
    assert r.status_code == 200, r.text
    code = r.json()["code"]

    # 2. the config flow in Home Assistant, with a real temperature sensor in °F
    # Two minutes old: the platform keeps the first reading within one minute, so the
    # change below must fall in a later minute to be the value that shows.
    hass.states.async_set(
        "sensor.livetest_kas_temperature", "70.7",
        {"device_class": "temperature", "unit_of_measurement": "°F", "friendly_name": "Livetest kas"},
        timestamp=time.time() - 120,
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: code.lower(), "advanced": {CONF_URL: URL}})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "entities", result
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: ["sensor.livetest_kas_temperature"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    adapter_id = entry.data["adapter_id"]

    try:
        # 3. a state change, pushed now
        hass.states.async_set(
            "sensor.livetest_kas_temperature", "72.5",
            {"device_class": "temperature", "unit_of_measurement": "°F", "friendly_name": "Livetest kas"},
        )
        await hass.async_block_till_done()
        pusher = entry.runtime_data
        await pusher.async_flush(force=True)
        assert len(pusher.queue) == 0, pusher.diagnostics()
        assert pusher.stats["last_error"] is None, pusher.stats

        # 4. visible for A: installation, device, measuring point in °C
        r = await hass.async_add_executor_job(get, a, "/fg-ha/v1/installaties")
        inst = [i for i in r.json()["installaties"] if i["adapter_id"] == adapter_id]
        assert len(inst) == 1
        ent = inst[0]["entiteiten"][0]
        assert ent["entity_id"] == "sensor.livetest_kas_temperature" and ent["soort"] == "temp_c"
        slug = ent["toestel"]
        assert slug
        r = await hass.async_add_executor_job(get, a, f"/fg-ent/v1/toestel/{slug}")
        assert r.status_code == 200, r.text
        mp = {m["soort"]: m for m in r.json()["meetpunten"]}
        assert abs(float(mp["temp_c"]["waarde"]) - 22.5) < 0.05, mp["temp_c"]  # 72.5 °F
        assert mp["temp_c"]["bron"] == "ha:sensor.livetest_kas_temperature"
        r = await hass.async_add_executor_job(get, a, "/metis/v1/devices")
        assert slug in {d["slug"] for d in r.json()["devices"]}

        # 5. NOT visible for B
        r = await hass.async_add_executor_job(get, b, f"/fg-ent/v1/toestel/{slug}")
        assert r.status_code in (401, 403, 404), r.status_code
        r = await hass.async_add_executor_job(get, b, "/metis/v1/devices")
        assert slug not in {d["slug"] for d in r.json()["devices"]}
        r = await hass.async_add_executor_job(get, b, "/fg-ha/v1/installaties")
        assert r.json()["installaties"] == []
        r = await hass.async_add_executor_job(get, b, "/fg-zb/v1/adapters/mine")
        assert adapter_id not in {x["id"] for x in r.json()["adapters"]}
    finally:
        # 6. removing the integration revokes it on FairGrow
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    r = await hass.async_add_executor_job(get, a, "/fg-ha/v1/installaties")
    inst = [i for i in r.json()["installaties"] if i["adapter_id"] == adapter_id]
    assert inst and inst[0]["revoked_at"], inst


LIVE_AUTOMATIONS = """\
- id: 'livetest_kas_water'
  alias: Livetest kas water geven
  description: Zoals de UI van Home Assistant hem schrijft
  triggers:
    - trigger: numeric_state
      entity_id: sensor.livetest_kas_vocht
      below: 30
      for: '00:05:00'
  conditions:
    - condition: time
      after: '06:00:00'
      before: '21:00:00'
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.livetest_klep
    - delay: '00:10:00'
    - action: switch.turn_off
      target:
        entity_id: switch.livetest_klep
  mode: single
- id: 'livetest_oud_formaat'
  alias: Livetest oud formaat
  trigger:
    platform: time
    at: '07:00:00'
  action:
    - service: notify.notify
      data:
        message: !secret livetest_tekst
"""

LIVE_SCRIPTS = """\
livetest_klep_puls:
  alias: Livetest klep vijf minuten
  sequence:
    - action: switch.turn_on
      target:
        entity_id: switch.livetest_klep
    - delay: '00:05:00'
    - action: switch.turn_off
      target:
        entity_id: switch.livetest_klep
"""


async def test_live_share_and_take_over_automations(hass: HomeAssistant, live_session: aiohttp.ClientSession) -> None:
    """Erik's route: pair with sharing on, the list arrives, take every item over.

    Taking over must never crash on a raw Home Assistant definition (string entity_id,
    old trigger/platform format, !secret as text) and B must not see A's list."""
    a = _session("FG_LIVE_A_USER", "FG_LIVE_A_PW")
    b = _session("FG_LIVE_B_USER", "FG_LIVE_B_PW")
    api = URL + "/wp-json"
    from pathlib import Path

    Path(hass.config.config_dir, "automations.yaml").write_text(LIVE_AUTOMATIONS, encoding="utf-8")
    Path(hass.config.config_dir, "scripts.yaml").write_text(LIVE_SCRIPTS, encoding="utf-8")

    def call(s: requests.Session, method: str, path: str, **kw) -> requests.Response:
        return s.request(method, api + path, timeout=60, **kw)

    r = await hass.async_add_executor_job(lambda: call(a, "POST", "/fg-ha/v1/pair/new", json={}))
    code = r.json()["code"]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: code, "advanced": {CONF_URL: URL}})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: [], "share_automations": True, "share_scripts": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    pusher = entry.runtime_data
    genomen: list[int] = []
    try:
        assert pusher.stats["automations_error"] is None, pusher.stats
        assert pusher.stats["automations_count"] == 3, pusher.stats

        r = await hass.async_add_executor_job(lambda: call(a, "GET", "/fg-aut/v1/ha/lijst"))
        lijst = r.json()
        assert lijst["stand"] == "done" and lijst["bron"] == "ha_integratie", lijst
        ids = {x["id"] for x in lijst["automations"]}
        assert ids == {"livetest_kas_water", "livetest_oud_formaat", "script.livetest_klep_puls"}, ids
        assert "!secret livetest_tekst" in str(lijst["automations"])

        r = await hass.async_add_executor_job(lambda: call(b, "GET", "/fg-aut/v1/ha/lijst"))
        assert not any(x.get("id", "").startswith(("livetest", "script.livetest")) for x in r.json().get("automations", []))

        # "Fetch from Home Assistant": the integration hears it in its next answer
        r = await hass.async_add_executor_job(lambda: call(a, "POST", "/fg-aut/v1/ha/vraag", json={}))
        assert r.json()["ok"] and any(g.get("ha_integratie") for g in r.json()["gevraagd"]), r.text
        pusher._last_heartbeat -= 61  # noqa: SLF001 - a minute later: the heartbeat is due
        await pusher.async_flush(force=True)  # its answer carries automations_sturen
        assert pusher._aut_asked  # noqa: SLF001
        await pusher.async_share_automations()
        assert not pusher._aut_asked and pusher.stats["automations_error"] is None  # noqa: SLF001

        uitkomst = {}
        for ha_id in sorted(ids):
            r = await hass.async_add_executor_job(
                lambda h=ha_id: call(a, "POST", "/fg-aut/v1/ha/overnemen", json={"ha_id": h}))
            assert r.status_code == 200, (ha_id, r.status_code, r.text[:300])
            j = r.json()
            uitkomst[ha_id] = j
            genomen += [o["id"] for o in j.get("opgeslagen", [])]
        print("OVERNEMEN", {k: ([(o["naam"], o["status"], o["actief"]) for o in v.get("opgeslagen", [])],
                                [g["rapport"] for g in v.get("geweigerd", [])], v.get("fouten")) for k, v in uitkomst.items()})
        assert genomen, uitkomst
        r = await hass.async_add_executor_job(lambda: call(a, "GET", "/fg-aut/v1/automations"))
        mijn = {x["id"]: x for x in r.json().get("automations", r.json() if isinstance(r.json(), list) else [])}
        for aid in genomen:
            assert aid in mijn and int(mijn[aid]["actief"]) == 0, mijn.get(aid)
    finally:
        for aid in genomen:
            await hass.async_add_executor_job(lambda i=aid: call(a, "DELETE", f"/fg-aut/v1/automations/{i}"))
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()


LIVE_WARM = """\
- id: livetest_warm_licht
  alias: Livetest warm licht aan
  triggers:
    - trigger: numeric_state
      entity_id: sensor.livetest_temp
      above: 20
  actions:
    - action: light.turn_on
      target:
        entity_id: light.livetest_strip
"""


async def test_live_fairgrow_switches_a_light(hass: HomeAssistant, live_session: aiohttp.ClientSession) -> None:
    """Erik's test (0.3.0): share a sensor and a light, let FairGrow switch the light, take
    the automation 'above 20 degrees: light on' over, switch it on, warm up the room.

    The platform must queue light.turn_on, the integration must fetch and run it, and the
    result must come back. B never sees A's commands."""
    from pathlib import Path

    from homeassistant.core import ServiceCall

    a = _session("FG_LIVE_A_USER", "FG_LIVE_A_PW")
    b = _session("FG_LIVE_B_USER", "FG_LIVE_B_PW")
    api = URL + "/wp-json"

    def call(s: requests.Session, method: str, path: str, **kw) -> requests.Response:
        return s.request(method, api + path, timeout=60, **kw)

    Path(hass.config.config_dir, "automations.yaml").write_text(LIVE_WARM, encoding="utf-8")
    hass.states.async_set("sensor.livetest_temp", "18.0",
                          {"device_class": "temperature", "unit_of_measurement": "°C", "friendly_name": "Livetest temp"})
    hass.states.async_set("light.livetest_strip", "off", {"friendly_name": "Livetest strip"})
    switched: list[ServiceCall] = []

    async def _light(service: ServiceCall) -> None:
        switched.append(service)
        hass.states.async_set("light.livetest_strip", "on" if service.service == "turn_on" else "off",
                              {"friendly_name": "Livetest strip"})

    hass.services.async_register("light", "turn_on", _light)
    hass.services.async_register("light", "turn_off", _light)

    r = await hass.async_add_executor_job(lambda: call(a, "POST", "/fg-ha/v1/pair/new", json={}))
    code = r.json()["code"]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: code, "advanced": {CONF_URL: URL}})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        CONF_ENTITIES: ["sensor.livetest_temp"], "control": ["light.livetest_strip"],
        "share_automations": True, "share_scripts": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    pusher = entry.runtime_data
    genomen: list[int] = []
    try:
        assert pusher.control == ["light.livetest_strip"], pusher.control
        assert pusher.stats["accepted_entities"] == 2, pusher.stats
        # the HA entities are in the automation register under their real names
        r = await hass.async_add_executor_job(lambda: call(a, "GET", "/fg-aut/v1/entiteiten"))
        ents = {e["entity_id"]: e for e in r.json().get("entiteiten", r.json() if isinstance(r.json(), list) else [])}
        assert "light.livetest_strip" in ents and int(ents["light.livetest_strip"]["stuurbaar"]) == 1, list(ents)[:20]
        assert "sensor.livetest_temp" in ents

        r = await hass.async_add_executor_job(lambda: call(a, "POST", "/fg-aut/v1/ha/overnemen", json={"ha_id": "livetest_warm_licht"}))
        j = r.json()
        print("OVERNEMEN", j)
        assert j.get("opgeslagen"), j
        aid = j["opgeslagen"][0]["id"]
        genomen.append(aid)
        r = await hass.async_add_executor_job(lambda: call(a, "POST", f"/fg-aut/v1/automations/{aid}/actief", json={"actief": 1}))
        assert r.json().get("ok"), r.text
        await hass.async_add_executor_job(lambda: call(a, "GET", "/fg-aut/v1/standen", params={"bij": 1}))

        # the room warms up
        hass.states.async_set("sensor.livetest_temp", "22.5",
                              {"device_class": "temperature", "unit_of_measurement": "°C", "friendly_name": "Livetest temp"})
        await hass.async_block_till_done()
        await pusher.async_flush(force=True)
        for _ in range(6):
            await pusher.async_commands()
            await hass.async_block_till_done()
            if switched:
                break
            await asyncio.sleep(2)
        r = await hass.async_add_executor_job(lambda: call(a, "GET", "/fg-ha/v1/opdrachten"))
        mijn = r.json()["opdrachten"]
        r = await hass.async_add_executor_job(lambda: call(a, "GET", f"/fg-aut/v1/automations/{aid}/log"))
        print("OPDRACHTEN", mijn[:3], "LOG", r.text[:600], "STATS", pusher.stats)
        assert switched and switched[0].service == "turn_on", pusher.stats
        assert hass.states.get("light.livetest_strip").state == "on"
        await pusher.async_commands()  # report the result if it was not yet
        r = await hass.async_add_executor_job(lambda: call(a, "GET", "/fg-ha/v1/opdrachten"))
        laatste = r.json()["opdrachten"][0]
        assert laatste["entity_id"] == "light.livetest_strip" and laatste["state"] == "done", laatste

        r = await hass.async_add_executor_job(lambda: call(b, "GET", "/fg-ha/v1/opdrachten"))
        assert not any(o["entity_id"].startswith("light.livetest") for o in r.json()["opdrachten"])
    finally:
        for i in genomen:
            await hass.async_add_executor_job(lambda i=i: call(a, "DELETE", f"/fg-aut/v1/automations/{i}"))
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()


ERIK_YAML = """\
- id: test_warm_light_on
  alias: Warm, light on
  triggers:
    - trigger: numeric_state
      entity_id: sensor.livetest_temp
      above: 20
  actions:
    - action: light.turn_on
      target:
        entity_id: light.livetest_strip
- id: test_cool_light_off
  alias: Cool, light off
  triggers:
    - trigger: numeric_state
      entity_id: sensor.livetest_temp
      below: 20
  actions:
    - action: light.turn_off
      target:
        entity_id: light.livetest_strip
"""


async def test_live_erik_route_on_and_off(hass: HomeAssistant, live_session: aiohttp.ClientSession) -> None:
    """The exact route in the mail to Erik, with the clock running for real.

    automations.yaml starts empty; the two automations are written AFTER pairing, then
    fetched the way the button on fairgrow.eu does it (ha/vraag, poll ha/lijst until it is
    no longer 'wacht'), taken over, switched on. Warm: light on. Cool: light off."""
    from pathlib import Path

    from homeassistant.core import ServiceCall

    a = _session("FG_LIVE_A_USER", "FG_LIVE_A_PW")
    api = URL + "/wp-json"

    def call(method: str, path: str, **kw) -> requests.Response:
        return a.request(method, api + path, timeout=60, **kw)

    def temp(v: str) -> None:
        hass.states.async_set("sensor.livetest_temp", v,
                              {"device_class": "temperature", "unit_of_measurement": "°C", "friendly_name": "Livetest temp"})

    Path(hass.config.config_dir, "automations.yaml").write_text("[]\n", encoding="utf-8")
    temp("18.0")
    hass.states.async_set("light.livetest_strip", "off", {"friendly_name": "Livetest strip"})
    switched: list[str] = []

    async def _light(service: ServiceCall) -> None:
        switched.append(service.service)
        hass.states.async_set("light.livetest_strip", "on" if service.service == "turn_on" else "off",
                              {"friendly_name": "Livetest strip"})

    hass.services.async_register("light", "turn_on", _light)
    hass.services.async_register("light", "turn_off", _light)

    code = (await hass.async_add_executor_job(lambda: call("POST", "/fg-ha/v1/pair/new", json={}))).json()["code"]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: code, "advanced": {CONF_URL: URL}})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        CONF_ENTITIES: ["sensor.livetest_temp"], "control": ["light.livetest_strip"],
        "share_automations": True, "share_scripts": False})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    pusher = entry.runtime_data
    genomen: list[int] = []
    try:
        # Erik writes his two automations now, after pairing
        Path(hass.config.config_dir, "automations.yaml").write_text(ERIK_YAML, encoding="utf-8")
        t0 = time.time()
        r = await hass.async_add_executor_job(lambda: call("POST", "/fg-aut/v1/ha/vraag", json={}))
        assert r.json()["ok"] and any(g.get("ha_integratie") for g in r.json()["gevraagd"]), r.text
        lijst = {}
        for _ in range(40):  # what the page does: every 3 s, at most 90 s (here 120 s)
            await asyncio.sleep(3)
            lijst = (await hass.async_add_executor_job(lambda: call("GET", "/fg-aut/v1/ha/lijst"))).json()
            if lijst.get("stand") == "done":
                break
        print("LIJST na", round(time.time() - t0), "s:", lijst.get("stand"), [x["id"] for x in lijst.get("automations", [])])
        assert lijst.get("stand") == "done", lijst
        assert {x["id"] for x in lijst["automations"]} == {"test_warm_light_on", "test_cool_light_off"}
        assert time.time() - t0 < 90, "slower than the page waits"

        for ha_id in ("test_warm_light_on", "test_cool_light_off"):
            j = (await hass.async_add_executor_job(lambda h=ha_id: call("POST", "/fg-aut/v1/ha/overnemen", json={"ha_id": h}))).json()
            assert j.get("opgeslagen") and j["opgeslagen"][0]["status"] == "ok", j
            genomen.append(j["opgeslagen"][0]["id"])
        for aid in genomen:
            j = (await hass.async_add_executor_job(lambda i=aid: call("POST", f"/fg-aut/v1/automations/{i}/actief", json={"actief": 1}))).json()
            assert j.get("ok"), j

        async def wacht_op(n: int) -> None:
            for _ in range(80):  # readings go up once a minute
                await asyncio.sleep(1)
                if len(switched) >= n:
                    return

        await asyncio.sleep(70)  # a full round of the engine after switching on: nothing may switch
        assert switched == [], f"switched on activation: {switched}"
        t1 = time.time()
        temp("22.5")
        await wacht_op(1)
        print("AAN na", round(time.time() - t1, 1), "s", switched)
        assert switched == ["turn_on"] and hass.states.get("light.livetest_strip").state == "on"

        await asyncio.sleep(4)  # past the 3 s brake per actuator
        t2 = time.time()
        temp("19.0")
        await wacht_op(2)
        print("UIT na", round(time.time() - t2, 1), "s", switched)
        assert switched == ["turn_on", "turn_off"] and hass.states.get("light.livetest_strip").state == "off"
        print("STATS", pusher.stats)
    finally:
        for i in genomen:
            await hass.async_add_executor_job(lambda i=i: call("DELETE", f"/fg-aut/v1/automations/{i}"))
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
