"""Config flow: paste a pairing code, then choose which entities to share."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntityFilterSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
)

from .api import FairGrowApi, FairGrowCodeError, FairGrowError, normalize_url
from .const import (
    BINARY_SENSOR_DEVICE_CLASSES,
    CONF_ADVANCED,
    CONF_ADAPTER_ID,
    CONF_CODE,
    CONF_CONTROL,
    CONF_ENTITIES,
    CONF_SHARE_AUTOMATIONS,
    CONF_SHARE_SCRIPTS,
    CONF_TOKEN,
    CONF_URL,
    CONTROL_DOMAINS,
    DEFAULT_URL,
    DOMAIN,
    SENSOR_DEVICE_CLASSES,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)


def share_schema(options: Mapping[str, Any]) -> vol.Schema:
    """What to share, what FairGrow may switch, and whether automations and scripts go along.

    Everything is off unless the user ticks it: until 0.2.0 only the chosen readings left
    Home Assistant, and an update must not change that by itself. Up to 0.2.0 one box
    covered automations and scripts together; whoever had it on keeps both on."""
    share_aut = bool(options.get(CONF_SHARE_AUTOMATIONS, False))
    return vol.Schema(
        {
            vol.Optional(CONF_ENTITIES, default=list(options.get(CONF_ENTITIES, []))): entity_selector(),
            vol.Optional(CONF_CONTROL, default=list(options.get(CONF_CONTROL, []))): control_selector(),
            vol.Optional(CONF_SHARE_AUTOMATIONS, default=share_aut): BooleanSelector(),
            vol.Optional(
                CONF_SHARE_SCRIPTS, default=bool(options.get(CONF_SHARE_SCRIPTS, share_aut))
            ): BooleanSelector(),
        }
    )


def share_options(user_input: dict[str, Any]) -> dict[str, Any]:
    """What the form gave back, as the options of the entry.

    What FairGrow may switch is always shared too: an automation that turns a lamp on
    needs to see that the lamp is on."""
    control = [e for e in user_input.get(CONF_CONTROL, []) if e.split(".", 1)[0] in CONTROL_DOMAINS]
    entities = list(user_input.get(CONF_ENTITIES, []))
    entities += [e for e in control if e not in entities]
    return {
        CONF_ENTITIES: entities,
        CONF_CONTROL: control,
        CONF_SHARE_AUTOMATIONS: bool(user_input.get(CONF_SHARE_AUTOMATIONS, False)),
        CONF_SHARE_SCRIPTS: bool(user_input.get(CONF_SHARE_SCRIPTS, False)),
    }


def entity_selector() -> EntitySelector:
    """Sensors and binary sensors with a device class FairGrow can use, plus lights and
    switches (their on/off state only)."""
    return EntitySelector(
        EntitySelectorConfig(
            multiple=True,
            filter=[
                EntityFilterSelectorConfig(
                    domain="sensor", device_class=list(SENSOR_DEVICE_CLASSES)
                ),
                EntityFilterSelectorConfig(
                    domain="binary_sensor",
                    device_class=list(BINARY_SENSOR_DEVICE_CLASSES),
                ),
                EntityFilterSelectorConfig(domain=list(CONTROL_DOMAINS)),
            ],
        )
    )


def control_selector() -> EntitySelector:
    """Lights and switches FairGrow may turn on and off."""
    return EntitySelector(
        EntitySelectorConfig(
            multiple=True,
            filter=[EntityFilterSelectorConfig(domain=list(CONTROL_DOMAINS))],
        )
    )


class FairGrowConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pairing with a FairGrow account."""

    VERSION = 1

    def __init__(self) -> None:
        """Nothing paired yet."""
        self._paired: dict[str, Any] | None = None
        self._url: str = DEFAULT_URL

    async def _async_pair(self, code: str, url: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
        errors: dict[str, str] = {}
        api = FairGrowApi(async_get_clientsession(self.hass), url)
        try:
            result = await api.pair(
                code.strip(), HA_VERSION, self.hass.config.location_name or "", VERSION
            )
        except FairGrowCodeError as err:
            errors[CONF_CODE] = err.reason
        except FairGrowError as err:
            _LOGGER.debug("Pairing failed: %s", err)
            errors["base"] = "cannot_connect"
        else:
            return result, errors
        return None, errors

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: the pairing code from fairgrow.eu."""
        errors: dict[str, str] = {}
        if user_input is not None:
            advanced = user_input.get(CONF_ADVANCED) or {}
            self._url = normalize_url(advanced.get(CONF_URL) or DEFAULT_URL)
            result, errors = await self._async_pair(user_input[CONF_CODE], self._url)
            if result is not None:
                self._paired = result
                await self.async_set_unique_id(f"{self._url}#{result['adapter_id']}")
                self._abort_if_unique_id_configured()
                return await self.async_step_entities()

        # The address only matters for a test site; it sits folded away.
        schema = vol.Schema(
            {
                vol.Required(CONF_CODE): TextSelector(),
                vol.Optional(CONF_ADVANCED): section(
                    vol.Schema({vol.Optional(CONF_URL, default=DEFAULT_URL): TextSelector()}),
                    {"collapsed": True},
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"site": "fairgrow.eu"},
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: which entities FairGrow may see."""
        assert self._paired is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._paired.get("label") or "FairGrow",
                data={
                    CONF_URL: self._url,
                    CONF_TOKEN: self._paired["token"],
                    CONF_ADAPTER_ID: self._paired["adapter_id"],
                },
                options=share_options(user_input),
            )
        return self.async_show_form(
            step_id="entities",
            data_schema=share_schema({}),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The token was refused: ask for a new pairing code."""
        self._url = entry_data.get(CONF_URL, DEFAULT_URL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair again, keep the chosen entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result, errors = await self._async_pair(user_input[CONF_CODE], self._url)
            if result is not None:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={
                        CONF_TOKEN: result["token"],
                        CONF_ADAPTER_ID: result["adapter_id"],
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_CODE): TextSelector()}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Change which entities are shared."""
        return FairGrowOptionsFlow()


class FairGrowOptionsFlow(OptionsFlow):
    """Choose again what is shared and what FairGrow may switch."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One step: the same form, filled with the current choice."""
        if user_input is not None:
            return self.async_create_entry(data=share_options(user_input))
        return self.async_show_form(
            step_id="init",
            data_schema=share_schema(self.config_entry.options),
        )
