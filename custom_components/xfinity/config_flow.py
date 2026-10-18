"""Config flow for Xfinity."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import XfinityAuthError, XfinityClient, XfinityClientRejected, XfinityError
from .const import ADDRESS_MAX_LENGTH, ADDRESS_MIN_LENGTH, CONF_ADDRESS, CONF_REFRESH_TOKEN, DOMAIN, TOKEN_MAX_LENGTH, TOKEN_MIN_LENGTH

USER_SCHEMA = vol.Schema({
    vol.Required(CONF_ADDRESS): str,
    vol.Required(CONF_REFRESH_TOKEN): str,
})

REAUTH_SCHEMA = vol.Schema({
    vol.Required(CONF_REFRESH_TOKEN): str,
})

ADDRESS_SCHEMA = vol.Schema({
    vol.Required(CONF_ADDRESS): str,
})


def clean_address(value: object) -> str:
    """Return a service address Home Assistant can send to the outage API."""
    if not isinstance(value, str):
        raise XfinityError("invalid address")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise XfinityError("invalid address")
    address = " ".join(value.split())
    if not ADDRESS_MIN_LENGTH <= len(address) <= ADDRESS_MAX_LENGTH:
        raise XfinityError("invalid address")
    return address


def clean_token(value: object) -> str:
    """Return a refresh token with no surrounding or embedded whitespace."""
    if not isinstance(value, str):
        raise XfinityError("invalid token")
    token = value.strip()
    if not TOKEN_MIN_LENGTH <= len(token) <= TOKEN_MAX_LENGTH:
        raise XfinityError("invalid token")
    if any(character.isspace() or not character.isprintable() for character in token):
        raise XfinityError("invalid token")
    return token


async def _verify(hass: HomeAssistant, address: str, refresh_token: str) -> XfinityClient:
    client = XfinityClient(async_get_clientsession(hass), address, refresh_token)
    await client.async_refresh()
    return client


class XfinityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Import a desktop refresh token. This flow never opens a browser."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                address = clean_address(user_input.get(CONF_ADDRESS))
                refresh = clean_token(user_input.get(CONF_REFRESH_TOKEN))
                client = await _verify(self.hass, address, refresh)
            except XfinityClientRejected:
                errors["base"] = "invalid_client"
            except XfinityAuthError:
                errors["base"] = "invalid_auth"
            except XfinityError as err:
                errors.update(_input_error(err))
            else:
                return self.async_create_entry(
                    title="Xfinity",
                    data={CONF_ADDRESS: address, CONF_REFRESH_TOKEN: client.refresh_token},
                )
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                refresh = clean_token(user_input.get(CONF_REFRESH_TOKEN))
                client = await _verify(self.hass, entry.data[CONF_ADDRESS], refresh)
            except XfinityClientRejected:
                errors["base"] = "invalid_client"
            except XfinityAuthError:
                errors["base"] = "invalid_auth"
            except XfinityError as err:
                errors.update(_input_error(err))
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_REFRESH_TOKEN: client.refresh_token},
                )
        return self.async_show_form(step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            try:
                address = clean_address(user_input.get(CONF_ADDRESS))
            except XfinityError:
                errors["address"] = "invalid_address"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_ADDRESS: address},
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                ADDRESS_SCHEMA,
                {CONF_ADDRESS: entry.data.get(CONF_ADDRESS, "")},
            ),
            errors=errors,
        )


def _input_error(err: XfinityError) -> dict[str, str]:
    message = str(err)
    if message == "invalid address":
        return {"address": "invalid_address"}
    if message == "invalid token":
        return {"refresh_token": "invalid_token"}
    return {"base": "cannot_connect"}
