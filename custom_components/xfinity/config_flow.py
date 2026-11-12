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
from .browser_auth import BrowserAuthorization, async_complete_browser_auth
from .direct_auth import XfinityDirectLoginSession
from .login import (
    BrowserlessError,
    XfinityBotChallengeError,
    XfinityBrowserSessionExpiredError,
    XfinityInvalidCallbackError,
    XfinityLoginError,
    XfinityLoginRejected,
    XfinityMfaRequired,
    async_browserless_login,
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
        vol.Optional(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
        vol.Optional(CONF_BROWSERLESS_API_KEY): str,
    }
)

BROWSER_SCHEMA = vol.Schema(
    {
        vol.Required("callback_url"): str,
    }
)

REAUTH_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PASSWORD): str,
        vol.Optional(CONF_BROWSERLESS_API_KEY): str,
    }
)

RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
        vol.Required(CONF_BROWSERLESS_API_KEY): str,
    }
)


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


async def _login(
    hass: HomeAssistant, username: str, password: str, api_key: str
) -> str:
    tokens = await async_browserless_login(
        async_get_clientsession(hass), username, password, api_key
    )
    refresh = tokens.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise XfinityLoginRejected("token exchange rejected")
    return refresh


class XfinityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in to Xfinity using Browserless, direct HTTP, or browser handoff."""

    VERSION = 2

    def __init__(self) -> None:
        self._address: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._browser_auth: BrowserAuthorization | None = None
        self._reauth_entry: object | None = None

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                address = clean_address(user_input.get(CONF_ADDRESS))
                self._address = address

                raw_username = user_input.get(CONF_USERNAME)
                raw_password = user_input.get(CONF_PASSWORD)
                raw_api_key = user_input.get(CONF_BROWSERLESS_API_KEY)

                username = (
                    clean_username(raw_username)
                    if raw_username is not None and str(raw_username).strip()
                    else None
                )
                password = (
                    clean_password(raw_password)
                    if raw_password is not None and str(raw_password).strip()
                    else None
                )
                api_key = (
                    clean_api_key(raw_api_key)
                    if raw_api_key is not None and str(raw_api_key).strip()
                    else None
                )

                # If raw values were provided but empty/invalid, clean_* would have raised.
                # If explicit invalid string was passed:
                if raw_username is not None and not username and raw_username != "":
                    username = clean_username(raw_username)
                if raw_password is not None and not password and raw_password != "":
                    password = clean_password(raw_password)
                if raw_api_key is not None and not api_key and raw_api_key != "":
                    api_key = clean_api_key(raw_api_key)

                self._username = username
                self._password = password

                if api_key and username and password:
                    refresh = await _login(self.hass, username, password, api_key)
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

                if username and password and not api_key:
                    try:
                        session = async_get_clientsession(self.hass)
                        direct_session = XfinityDirectLoginSession(session)
                        tokens = await direct_session.async_start(username, password)
                        refresh = tokens.get("refresh_token")
                        if not isinstance(refresh, str) or not refresh:
                            raise XfinityLoginRejected("token exchange rejected")
                        return self.async_create_entry(
                            title="Xfinity",
                            data={
                                CONF_ADDRESS: address,
                                CONF_USERNAME: username,
                                CONF_PASSWORD: password,
                                CONF_REFRESH_TOKEN: refresh,
                            },
                        )
                    except XfinityBotChallengeError:
                        return await self.async_step_browser()

                return await self.async_step_browser()
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

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_browser(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._browser_auth is None:
            self._browser_auth = BrowserAuthorization.create()

        if user_input is not None:
            callback_url = str(user_input.get("callback_url", "")).strip()
            try:
                session = async_get_clientsession(self.hass)
                tokens = await async_complete_browser_auth(
                    session, self._browser_auth, callback_url
                )
                refresh = tokens.get("refresh_token")
                if not isinstance(refresh, str) or not refresh:
                    raise XfinityLoginRejected("token exchange rejected")

                if self._reauth_entry is not None:
                    data_updates: dict[str, object] = {
                        CONF_REFRESH_TOKEN: refresh,
                    }
                    if self._password:
                        data_updates[CONF_PASSWORD] = self._password
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data_updates=data_updates,
                    )

                entry_data: dict[str, object] = {
                    CONF_ADDRESS: self._address or "",
                    CONF_REFRESH_TOKEN: refresh,
                }
                if self._username:
                    entry_data[CONF_USERNAME] = self._username
                if self._password:
                    entry_data[CONF_PASSWORD] = self._password

                return self.async_create_entry(
                    title="Xfinity",
                    data=entry_data,
                )
            except XfinityBrowserSessionExpiredError:
                self._browser_auth = BrowserAuthorization.create()
                errors["base"] = "session_expired"
            except XfinityInvalidCallbackError:
                errors["base"] = "invalid_callback"
            except XfinityLoginRejected:
                errors["base"] = "invalid_auth"
            except XfinityLoginError, TimeoutError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="browser",
            data_schema=BROWSER_SCHEMA,
            errors=errors,
            description_placeholders={
                "authorization_url": self._browser_auth.url,
            },
        )

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        self._reauth_entry = entry
        if user_input is not None:
            raw_password = user_input.get(CONF_PASSWORD)
            raw_api_key = user_input.get(CONF_BROWSERLESS_API_KEY)

            if not raw_password and not raw_api_key:
                return await self.async_step_browser()

            try:
                password = clean_password(raw_password)
                api_key = clean_api_key(raw_api_key)
                username = entry.data.get(CONF_USERNAME) or ""
                if not username:
                    raise XfinityError("invalid username")
                refresh = await _login(self.hass, username, password, api_key)
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: password,
                        CONF_BROWSERLESS_API_KEY: api_key,
                        CONF_REFRESH_TOKEN: refresh,
                    },
                )
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

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
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
                    data_updates={
                        CONF_ADDRESS: address,
                        CONF_BROWSERLESS_API_KEY: api_key,
                    },
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
