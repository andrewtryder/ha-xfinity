"""Config flow for Xfinity."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import XfinityError
from .browser_auth import BrowserAuthorization, async_complete_browser_auth
from .const import (
    ADDRESS_MAX_LENGTH,
    ADDRESS_MIN_LENGTH,
    AUTH_METHOD_BROWSER,
    AUTH_METHOD_BROWSERLESS,
    CONF_ADDRESS,
    CONF_AUTH_METHOD,
    CONF_BROWSERLESS_API_KEY,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
    TOKEN_MAX_LENGTH,
    TOKEN_MIN_LENGTH,
)
from .login import (
    BrowserlessError,
    XfinityBrowserSessionExpiredError,
    XfinityInvalidCallbackError,
    XfinityLoginError,
    XfinityLoginRejected,
    XfinityMfaRequired,
    async_browserless_login,
)

BROWSER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
    }
)

CALLBACK_SCHEMA = vol.Schema(
    {
        vol.Required("callback_url"): str,
    }
)

BROWSERLESS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
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
    """Sign in to Xfinity using browser handoff or Browserless."""

    VERSION = 2

    def __init__(self) -> None:
        self._address: str | None = None
        self._browser_auth: BrowserAuthorization | None = None
        self._reauth_entry: ConfigEntry | None = None
        self._reconfigure_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Handle initial user step by presenting sign-in options menu."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["browser", "browserless"],
        )

    async def async_step_browser(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Collect service address and start browser handoff."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry or self._reconfigure_entry
        suggested_address = self._address or (
            entry.data.get(CONF_ADDRESS, "") if entry is not None else ""
        )

        # For reauth where address is already known, advance straight to callback
        if self._reauth_entry is not None and suggested_address:
            self._address = suggested_address
            if self._browser_auth is None:
                self._browser_auth = BrowserAuthorization.create()
            return await self.async_step_browser_callback()

        if user_input is not None:
            try:
                self._address = clean_address(user_input.get(CONF_ADDRESS))
                self._browser_auth = BrowserAuthorization.create()
                return await self.async_step_browser_callback()
            except XfinityError as err:
                errors.update(_input_error(err))

        schema = self.add_suggested_values_to_schema(
            BROWSER_SCHEMA,
            {CONF_ADDRESS: suggested_address},
        )
        return self.async_show_form(
            step_id="browser",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_browser_callback(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Display authorization instructions and collect the OAuth callback URL."""
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

                entry = self._reauth_entry or self._reconfigure_entry
                if entry is not None:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={
                            CONF_AUTH_METHOD: AUTH_METHOD_BROWSER,
                            CONF_ADDRESS: self._address
                            or entry.data.get(CONF_ADDRESS, ""),
                            CONF_REFRESH_TOKEN: refresh,
                        },
                    )

                return self.async_create_entry(
                    title="Xfinity",
                    data={
                        CONF_AUTH_METHOD: AUTH_METHOD_BROWSER,
                        CONF_ADDRESS: self._address or "",
                        CONF_REFRESH_TOKEN: refresh,
                    },
                )
            except XfinityBrowserSessionExpiredError:
                self._browser_auth = BrowserAuthorization.create()
                errors["base"] = "session_expired"
            except XfinityInvalidCallbackError:
                errors["base"] = "invalid_callback"
            except XfinityLoginRejected:
                self._browser_auth = BrowserAuthorization.create()
                errors["base"] = "invalid_auth"
            except XfinityLoginError, TimeoutError:
                self._browser_auth = BrowserAuthorization.create()
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="browser_callback",
            data_schema=CALLBACK_SCHEMA,
            errors=errors,
            description_placeholders={
                "authorization_url": self._browser_auth.url,
            },
        )

    async def async_step_browserless(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Handle automated login using Browserless."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry or self._reconfigure_entry

        if user_input is not None:
            try:
                address = clean_address(user_input.get(CONF_ADDRESS))
                username = clean_username(user_input.get(CONF_USERNAME))
                password = clean_password(user_input.get(CONF_PASSWORD))
                api_key = clean_api_key(user_input.get(CONF_BROWSERLESS_API_KEY))

                refresh = await _login(self.hass, username, password, api_key)

                if entry is not None:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={
                            CONF_AUTH_METHOD: AUTH_METHOD_BROWSERLESS,
                            CONF_ADDRESS: address,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            CONF_BROWSERLESS_API_KEY: api_key,
                            CONF_REFRESH_TOKEN: refresh,
                        },
                    )

                return self.async_create_entry(
                    title="Xfinity",
                    data={
                        CONF_AUTH_METHOD: AUTH_METHOD_BROWSERLESS,
                        CONF_ADDRESS: address,
                        CONF_USERNAME: username,
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

        suggested: dict[str, str] = {}
        if entry is not None:
            suggested = {
                CONF_ADDRESS: entry.data.get(CONF_ADDRESS, ""),
                CONF_USERNAME: entry.data.get(CONF_USERNAME, ""),
                CONF_BROWSERLESS_API_KEY: entry.data.get(CONF_BROWSERLESS_API_KEY, ""),
            }

        schema = self.add_suggested_values_to_schema(BROWSERLESS_SCHEMA, suggested)
        return self.async_show_form(
            step_id="browserless",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        """Handle reauthorization by storing target entry and prompting user."""
        self._reauth_entry = self._get_reauth_entry()
        self._address = self._reauth_entry.data.get(CONF_ADDRESS)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Prompt user with authentication choices during reauth."""
        return self.async_show_menu(
            step_id="reauth_confirm",
            menu_options=["browser", "browserless"],
        )

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Allow user to update service address or switch authentication methods."""
        self._reconfigure_entry = self._get_reconfigure_entry()
        self._address = self._reconfigure_entry.data.get(CONF_ADDRESS)
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["address", "browser", "browserless"],
        )

    async def async_step_address(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Update only the service address, preserving authentication and method."""
        errors: dict[str, str] = {}
        entry = self._reconfigure_entry or self._get_reconfigure_entry()
        self._reconfigure_entry = entry

        if user_input is not None:
            try:
                address = clean_address(user_input.get(CONF_ADDRESS))
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_ADDRESS: address},
                )
            except XfinityError as err:
                errors.update(_input_error(err))

        schema = self.add_suggested_values_to_schema(
            BROWSER_SCHEMA,
            {CONF_ADDRESS: entry.data.get(CONF_ADDRESS, "")},
        )
        return self.async_show_form(
            step_id="address",
            data_schema=schema,
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
