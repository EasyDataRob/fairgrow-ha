"""Small client for the FairGrow platform (routes under /wp-json/fg-ha/v1)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import API_PATH

TIMEOUT = aiohttp.ClientTimeout(total=30)


class FairGrowError(Exception):
    """Base error; also used for network trouble (worth retrying)."""


class FairGrowAuthError(FairGrowError):
    """The token is unknown or revoked: a new pairing code is needed."""


class FairGrowCodeError(FairGrowError):
    """A pairing code was refused. `reason` says why."""

    def __init__(self, reason: str, message: str = "") -> None:
        """Remember the reason key for the config flow."""
        super().__init__(message or reason)
        self.reason = reason


class FairGrowRateLimited(FairGrowError):
    """The platform asks us to slow down."""


# Error codes of the platform mapped to the error keys of the config flow.
_CODE_REASONS = {
    "fg_ha_code_vorm": "invalid_code",
    "fg_ha_code_onbekend": "invalid_code",
    "fg_ha_code_gebruikt": "code_used",
    "fg_ha_code_verlopen": "code_expired",
    "fg_ha_code_voor_brug": "bridge_code",
    "fg_ha_te_vaak": "too_many_attempts",
}


def normalize_url(url: str) -> str:
    """Strip spaces and trailing slashes; default to https."""
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


class FairGrowApi:
    """Talks to one FairGrow site with one installation token."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, token: str | None = None
    ) -> None:
        """Set up the client."""
        self._session = session
        self._base = normalize_url(base_url) + API_PATH
        self._token = token

    async def _post(
        self, path: str, payload: dict[str, Any], auth: bool = True
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if auth and self._token:
            headers["X-Adapter-Token"] = self._token
        try:
            async with self._session.post(
                self._base + path, json=payload, headers=headers, timeout=TIMEOUT
            ) as resp:
                try:
                    body = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    body = None
                if not isinstance(body, dict):
                    body = {}
                if resp.status in (401, 403):
                    raise FairGrowAuthError(str(body.get("message") or resp.status))
                if resp.status == 429:
                    if body.get("code") in _CODE_REASONS:
                        raise FairGrowCodeError(
                            _CODE_REASONS[body["code"]], str(body.get("message", ""))
                        )
                    raise FairGrowRateLimited(str(body.get("message") or "429"))
                if resp.status >= 400:
                    code = str(body.get("code", ""))
                    if code in _CODE_REASONS:
                        raise FairGrowCodeError(
                            _CODE_REASONS[code], str(body.get("message", ""))
                        )
                    raise FairGrowError(f"HTTP {resp.status}: {body.get('message', '')}")
                return body
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise FairGrowError(f"cannot reach FairGrow: {err}") from err

    async def pair(
        self, code: str, ha_version: str, name: str, integration_version: str
    ) -> dict[str, Any]:
        """Exchange a pairing code for an installation token."""
        body = await self._post(
            "/pair",
            {
                "code": code,
                "ha_versie": ha_version,
                "naam": name,
                "integratie_versie": integration_version,
            },
            auth=False,
        )
        if not body.get("token"):
            raise FairGrowError("FairGrow did not return a token")
        return body

    async def entities(self, entities: list[dict[str, Any]]) -> dict[str, Any]:
        """Announce the shared entities (the full list)."""
        return await self._post("/entities", {"entiteiten": entities})

    async def states(self, states: list[dict[str, Any]]) -> dict[str, Any]:
        """Push a batch of readings."""
        return await self._post("/states", {"states": states})

    async def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Tell FairGrow we are alive."""
        return await self._post("/heartbeat", payload)

    async def unpair(self) -> dict[str, Any]:
        """Revoke this installation on FairGrow."""
        return await self._post("/unpair", {})
