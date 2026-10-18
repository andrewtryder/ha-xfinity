"""Config flow for Xfinity."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import XfinityError
from .const import (
    ADDRESS_MAX_LENGTH,
    ADDRESS_MIN_LENGTH,
    CONF_ADDRESS,
    CONF_BROWSERLESS_API_KEY,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
    TOKEN_MAX_LENGTH,
    TOKEN_MIN_LENGTH,
)
from .login import BrowserlessError, XfinityLoginError, XfinityLoginRejected, XfinityMfaRequired, async_browserless_login

USER_SCHEMA = vol.Schema({
    vol.Required(CONF_ADDRESS): str,
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Required(CONF_BROWSERLESS_API_KEY): str,
})

REAUTH_SCHEMA = vol.Schema({
    vol.Required(CONF_PASSWORD): str,
    vol.Required(CONF_BROWSERLESS_API_KEY): str,
})

RECONFIGURE_SCHEMA = vol.Schema({
    vol.Required(CONF_ADDRESS): str,
    vol.Required(CONF_BROWSERLESS_API_KEY): str,
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


def clean_secret(value: object, *, minimum: int, kind: str) -> str:
    """Return a secret with no surrounding whitespace or control characters."""
    if not isinstance(value, str):
        raise XfinityError(kind)
    secret = value.strip()
    if not minimum <= len(secret) <= TOKEN_MAX_LENGTH:
        raise XfinityError(kind)
    if any(ord(character) < 32 or ord(character) == 127 for character in secret):
        raise XfinityError(kind)
    return secret


def clean_username(value: object) -> str:
    return clean_secret(value, minimum=3, kind="invalid username")


def clean_password(value: object) -> str:
    return clean_secret(value, minimum=1, kind="invalid password")


def clean_api_key(value: object) -> str:
    return clean_secret(value, minimum=TOKEN_MIN_LENGTH, kind="invalid browserless key")


async def _login(hass: HomeAssistant, username: str, password: str, api_key: str) -> str:
    tokens = await async_browserless_login(async_get_clientsession(hass), username, password, api_key)
    refresh = tokens.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise XfinityLoginRejected("token exchange rejected")
    return refresh


class XfinityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in through Browserless. Home Assistant does not open a browser."""

    VERSION = 2

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                address = clean_address(user_input.get(CONF_ADDRESS))
                username = clean_username(user_input.get(CONF_USERNAME))
                password = clean_password(user_input.get(CONF_PASSWORD))
                api_key = clean_api_key(user_input.get(CONF_BROWSERLESS_API_KEY))
                refresh = await _login(self.hass, username, password, api_key)
            except XfinityMfaRequired:
                errors["base"] = "mfa_required"
            except XfinityLoginRejected:
                errors["base"] = "invalid_auth"
            except BrowserlessError:
                errors["base"] = "browserless_error"
            except XfinityLoginError:
                errors["base"] = "cannot_connect"
            except XfinityError as err:
                errors.update(_input_error(err))
            else:
                return self.async_create_entry(
                    title="Xfinity",
                    data={
                        CONF_ADDRESS: address,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_BROWSERLESS_API_KEY: api_key,
                        CONF_REFRESH_TOKEN: refresh,
                    },
                )
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                password = clean_password(user_input.get(CONF_PASSWORD))
                api_key = clean_api_key(user_input.get(CONF_BROWSERLESS_API_KEY))
                username = entry.data.get(CONF_USERNAME) or ""
                if not username:
                    raise XfinityError("invalid username")
                refresh = await _login(self.hass, username, password, api_key)
            except XfinityMfaRequired:
                errors["base"] = "mfa_required"
            except XfinityLoginRejected:
                errors["base"] = "invalid_auth"
            except BrowserlessError:
                errors["base"] = "browserless_error"
            except XfinityLoginError:
                errors["base"] = "cannot_connect"
            except XfinityError as err:
                errors.update(_input_error(err))
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: password,
                        CONF_BROWSERLESS_API_KEY: api_key,
                        CONF_REFRESH_TOKEN: refresh,
                    },
                )
        return self.async_show_form(step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            try:
                address = clean_address(user_input.get(CONF_ADDRESS))
                api_key = clean_api_key(user_input.get(CONF_BROWSERLESS_API_KEY))
            except XfinityError as err:
                errors.update(_input_error(err))
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_ADDRESS: address, CONF_BROWSERLESS_API_KEY: api_key},
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                RECONFIGURE_SCHEMA,
                {
                    CONF_ADDRESS: entry.data.get(CONF_ADDRESS, ""),
                },
            ),
            errors=errors,
        )


def _input_error(err: XfinityError) -> dict[str, str]:
    message = str(err)
    if message == "invalid address":
        return {"address": "invalid_address"}
    if message == "invalid username":
        return {"username": "invalid_username"}
    if message == "invalid password":
        return {"password": "invalid_password"}
    if message == "invalid browserless key":
        return {"browserless_api_key": "invalid_browserless_key"}
    return {"base": "cannot_connect"}
