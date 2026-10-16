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

import os
import socket
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
    hass.states.async_set(
        "sensor.livetest_kas_temperature", "70.7",
        {"device_class": "temperature", "unit_of_measurement": "°F", "friendly_name": "Livetest kas"},
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
