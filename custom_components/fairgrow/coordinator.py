"""Collects state changes of the shared entities and pushes them to FairGrow.

The pusher listens to `state_changed` for the entities the user chose, puts every
change in a queue and sends the queue in batches every PUSH_INTERVAL seconds. When
FairGrow cannot be reached the queue stays (up to MAX_QUEUE readings, saved over a
restart of Home Assistant) and is sent later, with backoff.

When the user opted in, it also sends the definitions of their own automations and
scripts (see automations.py), so that FairGrow can offer to take them over. That runs
beside the readings and never holds them up: a failure there is only counted.

Since 0.3.0 FairGrow can switch the lights and switches the user put on the control list
(see commands.py). While that list is not empty the pusher asks FairGrow every tick for
commands, runs the allowed ones and reports back. A light or switch that changes is sent
on the next tick instead of after a minute, so that an automation on FairGrow sees it.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import automations as own_automations, commands as own_commands
from .api import FairGrowApi, FairGrowAuthError, FairGrowError, FairGrowNotSupported
from .const import (
    AUTOMATIONS_CHECK_INTERVAL,
    AUTOMATIONS_RESEND_INTERVAL,
    BACKOFF_MAX,
    BACKOFF_MIN,
    COMMAND_TIMEOUT,
    CONF_CONTROL,
    CONF_ENTITIES,
    CONF_SHARE_AUTOMATIONS,
    CONF_SHARE_SCRIPTS,
    CONTROL_DOMAINS,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
    HEARTBEAT_INTERVAL,
    MAX_BATCH,
    MAX_COMMANDS,
    MAX_QUEUE,
    PUSH_INTERVAL,
    REANNOUNCE_INTERVAL,
    STORAGE_VERSION,
    TICK_INTERVAL,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)


def _now() -> float:
    """Wall clock in seconds; follows the test clock of Home Assistant too."""
    return dt_util.utcnow().timestamp()

_IEEE = re.compile(r"(?:0x)?([0-9a-f]{16})\b")
_IEEE_COLON = re.compile(r"^([0-9a-f]{2}:){7}[0-9a-f]{2}$")


def _ieee_of(device: dr.DeviceEntry | None) -> str:
    """The Zigbee address of a ZHA or Zigbee2MQTT device, if Home Assistant knows it.

    Only used so that a device that reached FairGrow earlier through the FairGrow
    bridge keeps its history. FairGrow never needs it otherwise."""
    if device is None:
        return ""
    for domain, value in list(device.identifiers) + list(device.connections):
        val = str(value).lower()
        if domain in ("zha", "zigbee") and _IEEE_COLON.match(val):
            return "0x" + val.replace(":", "")
        if domain in ("zha", "zigbee", "mqtt"):
            m = _IEEE.search(val.replace(":", ""))
            if m and ("zigbee" in val or domain != "mqtt"):
                return "0x" + m.group(1)
    return ""


class FairGrowPusher:
    """One per config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Set up the pusher; nothing runs until async_start."""
        self.hass = hass
        self.entry = entry
        self.api = FairGrowApi(
            async_get_clientsession(hass),
            entry.data.get(CONF_URL, DEFAULT_URL),
            entry.data[CONF_TOKEN],
        )
        self.entities: list[str] = list(entry.options.get(CONF_ENTITIES, []))
        self.queue: deque[dict[str, Any]] = deque(maxlen=MAX_QUEUE)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.queue"
        )
        self._unsubs: list[Any] = []
        self._registered = False
        self._last_announce = 0.0
        self._last_push = 0.0
        self._last_heartbeat = 0.0
        self._next_try = 0.0
        self._backoff = 0
        self._busy = False
        self._auth_failed = False
        self.push_interval = PUSH_INTERVAL
        self.share_automations = bool(entry.options.get(CONF_SHARE_AUTOMATIONS, False))
        # Up to 0.2.0 one box covered both files; without the new key scripts follow it.
        self.share_scripts = bool(
            entry.options.get(CONF_SHARE_SCRIPTS, self.share_automations)
        )
        self.share_own = self.share_automations or self.share_scripts
        self.control: list[str] = [
            e for e in entry.options.get(CONF_CONTROL, []) if e in self.entities
        ]
        self._urgent = False
        self._cmd_busy = False
        self._cmd_results: list[dict[str, Any]] = []
        # Ids that already ran. Should FairGrow hand a command out twice (a lost answer,
        # a retry), the lamp still switches once.
        self._cmd_seen: deque[Any] = deque(maxlen=200)
        self._cmd_paused_until = 0.0
        self._aut_file_fp: str | None = None
        self._aut_sent_fp: str | None = None
        self._aut_last_check = 0.0
        self._aut_last_sent = 0.0
        self._aut_asked = False
        self._aut_busy = False
        self.stats: dict[str, Any] = {
            "sent": 0,
            "failed_pushes": 0,
            "dropped_full_queue": 0,
            "last_push": None,
            "last_error": None,
            "accepted_entities": 0,
            "ignored_entities": {},
            "automations_shared": self.share_automations,
            "scripts_shared": self.share_scripts,
            "control": list(self.control),
            "commands_done": 0,
            "commands_refused": 0,
            "commands_failed": 0,
            "last_command": None,
            "commands_error": None,
            "automations_last_sent": None,
            "automations_count": None,
            "automations_sources": {},
            "automations_error": None,
        }

    # ------------------------------------------------------------------ life cycle

    async def async_start(self) -> None:
        """Load the saved queue, start listening and announce the entities.

        Raises FairGrowAuthError when the token is no longer valid, so that the
        caller can start a reauth. Network trouble is not fatal: readings queue up."""
        stored = await self._store.async_load()
        if stored and isinstance(stored.get("queue"), list):
            for item in stored["queue"][-MAX_QUEUE:]:
                if isinstance(item, dict) and item.get("entity_id") in self.entities:
                    self.queue.append(item)
        if self.entities:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, self.entities, self._async_state_changed
                )
            )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_tick, timedelta(seconds=TICK_INTERVAL)
            )
        )
        # What the sensors say right now, so that FairGrow does not wait for the
        # first change (a soil sensor may stay quiet for an hour).
        for entity_id in self.entities:
            state = self.hass.states.get(entity_id)
            if state is not None:
                self._enqueue(state)
        try:
            await self.async_announce()
            await self.async_flush(force=True)
        except FairGrowAuthError:
            raise
        except FairGrowError as err:
            _LOGGER.warning("FairGrow not reachable yet, readings are queued: %s", err)
        if self.share_own and not self._auth_failed:
            await self.async_share_automations(force=True)

    async def async_stop(self) -> None:
        """Stop listening and keep what is still queued for the next start."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save({"queue": list(self.queue)})

    # ------------------------------------------------------------------ collecting

    @callback
    def _enqueue(self, state: State) -> None:
        if len(self.queue) == self.queue.maxlen:
            self.stats["dropped_full_queue"] += 1
        self.queue.append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "ts": int(state.last_updated.timestamp()),
            }
        )

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None:
            return
        old_state = event.data["old_state"]
        # An attribute-only update carries no new reading.
        if old_state is not None and old_state.state == new_state.state:
            return
        self._enqueue(new_state)
        if new_state.domain in CONTROL_DOMAINS:
            self._urgent = True

    # ------------------------------------------------------------------ sending

    def entity_payload(self) -> list[dict[str, Any]]:
        """What FairGrow gets to know about each shared entity, and nothing more."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        out: list[dict[str, Any]] = []
        for entity_id in self.entities:
            state = self.hass.states.get(entity_id)
            entry = ent_reg.async_get(entity_id)
            attrs = state.attributes if state is not None else {}
            device_class = attrs.get("device_class") or (
                (entry.device_class or entry.original_device_class) if entry else None
            )
            unit = attrs.get("unit_of_measurement") or (
                entry.unit_of_measurement if entry else None
            )
            name = state.name if state is not None else (
                (entry.name or entry.original_name) if entry else None
            )
            device = (
                dev_reg.async_get(entry.device_id)
                if entry is not None and entry.device_id
                else None
            )
            area_id = (entry.area_id if entry is not None else None) or (
                device.area_id if device is not None else None
            )
            area = area_reg.async_get_area(area_id) if area_id else None
            item: dict[str, Any] = {
                "entity_id": entity_id,
                "device_class": str(device_class or ""),
                "eenheid": str(unit or ""),
                "naam": str(name or entity_id),
                "bedienbaar": entity_id in self.control,
            }
            if device is not None:
                item["toestel"] = {
                    "id": device.id,
                    "naam": device.name_by_user or device.name or "",
                    "gebied": area.name if area else "",
                    "fabrikant": device.manufacturer or "",
                    "model": device.model or "",
                    "ieee": _ieee_of(device),
                }
            elif area is not None:
                item["toestel"] = {"gebied": area.name}
            out.append(item)
        return out

    async def async_announce(self) -> None:
        """Send the full list of shared entities."""
        result = await self.api.entities(self.entity_payload())
        self._registered = True
        self._last_announce = _now()
        self.stats["accepted_entities"] = int(result.get("aangemeld", 0))
        self.stats["ignored_entities"] = {
            e["entity_id"]: e.get("reden", "")
            for e in result.get("entiteiten", [])
            if e.get("status") != "ok"
        }
        if self.stats["ignored_entities"]:
            _LOGGER.info(
                "FairGrow cannot use these entities: %s", self.stats["ignored_entities"]
            )
        self._apply_answer(result)

    def _apply_answer(self, result: dict[str, Any]) -> None:
        """What FairGrow says back: the push interval, and whether it wants the list."""
        sec = result.get("push_seconden")
        if isinstance(sec, int) and 10 <= sec <= 3600:
            self.push_interval = sec
        if result.get("automations_sturen"):
            self._aut_asked = True

    async def async_flush(self, force: bool = False) -> None:
        """Send what is queued, in batches. Leaves the queue alone on failure."""
        if self._busy or self._auth_failed:
            return
        now = _now()
        if not force and now < self._next_try:
            return
        self._busy = True
        try:
            if not self._registered or now - self._last_announce > REANNOUNCE_INTERVAL:
                await self.async_announce()
            while self.queue:
                batch = [self.queue[i] for i in range(min(MAX_BATCH, len(self.queue)))]
                result = await self.api.states(batch)
                for _ in batch:
                    self.queue.popleft()
                self.stats["sent"] += len(batch)
                self._apply_answer(result)
                if result.get("opnieuw_aanmelden"):
                    self._registered = False
            self._last_push = now
            self.stats["last_push"] = datetime.now().isoformat(timespec="seconds")
            self._backoff = 0
            self._next_try = 0
            # With sharing on, FairGrow's "fetch again" travels in the answers. Without new
            # readings the heartbeat is the only answer, so it comes every minute then.
            heartbeat_every = AUTOMATIONS_CHECK_INTERVAL if self.share_own else HEARTBEAT_INTERVAL
            if now - self._last_heartbeat >= heartbeat_every:
                result = await self.api.heartbeat(
                    {
                        "versie": VERSION,
                        "ha_versie": HA_VERSION,
                        "wachtrij": len(self.queue),
                        "entiteiten": len(self.entities),
                        "fouten": self.stats["failed_pushes"],
                        "automations_delen": self.share_automations,
                        "scripts_delen": self.share_scripts,
                        "schakelen": len(self.control),
                    }
                )
                self._apply_answer(result)
                self._last_heartbeat = now
        except FairGrowAuthError as err:
            self._auth_failed = True
            self.stats["last_error"] = f"auth: {err}"
            _LOGGER.error("FairGrow refused the token; pair again with a new code")
            self.entry.async_start_reauth(self.hass)
        except FairGrowError as err:
            self.stats["failed_pushes"] += 1
            self.stats["last_error"] = str(err)
            self._backoff = min(BACKOFF_MAX, max(BACKOFF_MIN, self._backoff * 2))
            self._next_try = _now() + self._backoff
            _LOGGER.debug("Push to FairGrow failed, retry in %s s: %s", self._backoff, err)
        finally:
            self._busy = False

    async def _async_tick(self, now: datetime) -> None:
        if self._urgent or now.timestamp() - self._last_push >= self.push_interval:
            self._urgent = False
            await self.async_flush()
        if self.control and not self._auth_failed:
            await self.async_commands()
        if self.share_own and not self._auth_failed:
            await self.async_share_automations()

    # ------------------------------------------------------------------ commands

    async def async_commands(self) -> None:
        """Report the results of the last round and run what FairGrow wants now.

        Never raises: switching must not hold up the readings, and a failed round is
        simply tried again on the next tick (the results wait for it)."""
        if self._cmd_busy or _now() < self._cmd_paused_until:
            return
        self._cmd_busy = True
        try:
            for _ in range(3):  # new commands may come back with the answer to the results
                results, self._cmd_results = self._cmd_results, []
                try:
                    answer = await self.api.commands(results)
                except FairGrowError:
                    self._cmd_results = results + self._cmd_results
                    raise
                # This answer comes every ten seconds, the heartbeat once a minute: FairGrow
                # asks for the automations list here too, so "Fetch" on fairgrow.eu is quick.
                self._apply_answer(answer)
                todo = [c for c in answer.get("opdrachten", []) if isinstance(c, dict)]
                if not todo:
                    break
                fresh = [c for c in todo[:MAX_COMMANDS] if c.get("id") not in self._cmd_seen]
                if not fresh:
                    break
                for command in fresh:
                    self._cmd_seen.append(command.get("id"))
                    self._cmd_results.append(await self._async_run(command))
            self.stats["commands_error"] = None
        except FairGrowNotSupported:
            self._cmd_paused_until = _now() + 3600
            self.stats["commands_error"] = "FairGrow does not send commands yet"
        except FairGrowAuthError as err:
            self.stats["commands_error"] = f"auth: {err}"
        except FairGrowError as err:
            self.stats["commands_error"] = str(err)
            _LOGGER.debug("Fetching commands from FairGrow failed: %s", err)
        finally:
            self._cmd_busy = False

    async def _async_run(self, command: dict[str, Any]) -> dict[str, Any]:
        """Run one command if the rules allow it; the result goes back to FairGrow."""
        result: dict[str, Any] = {"id": command.get("id")}
        try:
            domain, service, entity_id, data = own_commands.check(
                command, self.control, _now()
            )
        except own_commands.Refused as err:
            self.stats["commands_refused"] += 1
            _LOGGER.info("FairGrow command refused: %s", err)
            return {**result, "ok": False, "geweigerd": True, "fout": str(err)}
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await self.hass.services.async_call(
                    domain, service, data, blocking=True, target={"entity_id": entity_id}
                )
        except (HomeAssistantError, TimeoutError) as err:
            self.stats["commands_failed"] += 1
            text = str(err) or "timeout"
            return {**result, "ok": False, "fout": f"{domain}.{service}: {text}"[:240]}
        self.stats["commands_done"] += 1
        self.stats["last_command"] = f"{domain}.{service} {entity_id}"
        state = self.hass.states.get(entity_id)
        return {**result, "ok": True, "stand": state.state if state is not None else None}

    # ------------------------------------------------------------------ own automations

    async def async_share_automations(self, force: bool = False) -> None:
        """Send the definitions when they changed, when FairGrow asked, or after a while.

        `force` sends regardless (start-up). Never raises: the readings matter more."""
        if self._aut_busy:
            return
        now = _now()
        if not force and not self._aut_asked and now - self._aut_last_check < AUTOMATIONS_CHECK_INTERVAL:
            return
        self._aut_busy = True
        self._aut_last_check = now
        config_dir = self.hass.config.config_dir
        try:
            file_fp = await self.hass.async_add_executor_job(
                own_automations.fingerprint, config_dir, self.share_automations, self.share_scripts
            )
            due = (
                force
                or self._aut_asked
                or file_fp != self._aut_file_fp
                or now - self._aut_last_sent >= AUTOMATIONS_RESEND_INTERVAL
            )
            if not due:
                return
            payload = await self.hass.async_add_executor_job(
                own_automations.read_all, config_dir, self.share_automations, self.share_scripts
            )
            self.stats["automations_sources"] = payload["bronnen"]
            if (
                not force
                and not self._aut_asked
                and payload["vingerafdruk"] == self._aut_sent_fp
                and now - self._aut_last_sent < AUTOMATIONS_RESEND_INTERVAL
            ):
                # A file was touched but nothing in it changed.
                self._aut_file_fp = file_fp
                return
            payload["versie"] = VERSION
            await self.api.automations(payload)
            self._aut_file_fp = file_fp
            self._aut_sent_fp = payload["vingerafdruk"]
            self._aut_last_sent = now
            self._aut_asked = False
            self.stats["automations_count"] = len(payload["automations"])
            self.stats["automations_last_sent"] = datetime.now().isoformat(timespec="seconds")
            self.stats["automations_error"] = None
        except FairGrowNotSupported:
            # An older FairGrow: try again after the resend interval, quietly.
            self._aut_last_sent = now
            self._aut_file_fp = None
            self._aut_asked = False
            self.stats["automations_error"] = "FairGrow does not accept automations yet"
        except FairGrowAuthError as err:
            self.stats["automations_error"] = f"auth: {err}"
        except FairGrowError as err:
            # Retried on the next check; the file fingerprint is left unchanged on purpose.
            self.stats["automations_error"] = str(err)
            _LOGGER.debug("Sending automations to FairGrow failed: %s", err)
        except Exception as err:  # noqa: BLE001 - reading the user's files must never break the readings
            self.stats["automations_error"] = f"read: {err}"
            _LOGGER.warning("Could not read automations.yaml or scripts.yaml: %s", err)
        finally:
            self._aut_busy = False

    # ------------------------------------------------------------------ diagnostics

    def diagnostics(self) -> dict[str, Any]:
        """State of the pusher without the token."""
        return {
            "entities": list(self.entities),
            "queued": len(self.queue),
            "push_interval": self.push_interval,
            "registered": self._registered,
            "auth_failed": self._auth_failed,
            "backoff_s": self._backoff,
            "stats": dict(self.stats),
        }
