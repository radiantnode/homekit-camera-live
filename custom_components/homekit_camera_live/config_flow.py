"""Set up from the UI: nothing to fill in, and one option."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import CONF_SRTP_PORT, DEFAULT_SRTP_PORT, DOMAIN
from .server import async_find_binary


class HomeKitCameraLiveConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if await async_find_binary(self.hass) is None:
            return self.async_abort(reason="no_go2rtc")
        if user_input is not None:
            return self.async_create_entry(title="HomeKit Camera Live", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HomeKitCameraLiveOptionsFlow()


class HomeKitCameraLiveOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        port = self.config_entry.options.get(CONF_SRTP_PORT, DEFAULT_SRTP_PORT)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_SRTP_PORT, default=port): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535))}
            ),
        )
