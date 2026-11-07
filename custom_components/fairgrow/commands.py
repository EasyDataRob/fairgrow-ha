"""Deciding whether a command from FairGrow may run here.

FairGrow can only switch what the user put on the list "FairGrow may switch these" in
Home Assistant. That list lives in Home Assistant, not on fairgrow.eu: whoever gets hold
of the FairGrow account still cannot reach a lock, an alarm or a heater that was never
ticked here. On top of that only on, off and toggle of a light or a switch are possible,
and a command that took too long to arrive is refused.

Everything in this module is pure, so that the rules can be tested without a Home
Assistant around them; the coordinator does the actual service call.
"""

from __future__ import annotations

from typing import Any

from .const import COMMAND_MAX_AGE, CONTROL_DOMAINS, CONTROL_SERVICES, LIGHT_DATA_KEYS


class Refused(Exception):
    """The command does not run. The text goes back to FairGrow and into its log."""


def check(
    command: dict[str, Any], allowed: list[str], now: float
) -> tuple[str, str, str, dict[str, Any]]:
    """(domain, service, entity_id, data) for a command that may run, or raise Refused.

    The domain is always the domain of the entity itself: a "switch.turn_on" for a light
    becomes light.turn_on, and nothing FairGrow sends can make it call another domain."""
    entity_id = str(command.get("entity_id") or "").strip().lower()
    if entity_id not in allowed:
        raise Refused(f"{entity_id or 'no entity'} is not on the list FairGrow may switch")
    domain = entity_id.split(".", 1)[0]
    if domain not in CONTROL_DOMAINS:
        raise Refused(f"{entity_id}: FairGrow only switches lights and switches")

    service = str(command.get("dienst") or "").strip().lower()
    if "." in service:
        service = service.split(".", 1)[1]
    if service not in CONTROL_SERVICES:
        raise Refused(f"{service or 'no service'}: FairGrow may only turn on, turn off or toggle")

    made = command.get("gemaakt")
    if not isinstance(made, (int, float)) or now - float(made) > COMMAND_MAX_AGE:
        raise Refused(f"too old, not switched (older than {COMMAND_MAX_AGE} s)")

    data: dict[str, Any] = {}
    raw = command.get("data")
    if domain == "light" and service == "turn_on" and isinstance(raw, dict):
        data = {k: v for k, v in raw.items() if k in LIGHT_DATA_KEYS}
    return domain, service, entity_id, data
