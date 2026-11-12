"""Config flow tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xfinity import async_migrate_entry
from custom_components.xfinity.api import XfinityError
from custom_components.xfinity.config_flow import (
    _input_error,
    clean_address,
    clean_api_key,
    clean_password,
    clean_username,
)
from custom_components.xfinity.const import (
    AUTH_METHOD_BROWSER,
    AUTH_METHOD_BROWSERLESS,
    CONF_ADDRESS,
    CONF_AUTH_METHOD,
    CONF_BROWSERLESS_API_KEY,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.xfinity.login import (
    BrowserlessError,
    XfinityBrowserSessionExpiredError,
    XfinityInvalidCallbackError,
    XfinityLoginError,
    XfinityLoginRejected,
    XfinityMfaRequired,
)

ADDRESS = "21 Main Street"
USERNAME = "user@example.com"
PASSWORD = "correct-password"
API_KEY = "browserless-api-key"
TOKEN = "refresh-token-value"
CALLBACK_URL = "xfinitydigitalhome://auth?code=oi-test12345&state=s"
INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "xfinity"


def test_config_fields_have_translated_descriptions():
    """Every displayed config field explains the value expected from the user."""
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    translation = json.loads((INTEGRATION_DIR / "translations" / "en.json").read_text())
    assert translation == strings

    for step in strings["config"]["step"].values():
        if "data" in step:
            assert set(step["data_description"]) == set(step["data"])
        if "menu_options" in step:
            assert isinstance(step["menu_options"], dict)


def test_input_validation():
    assert clean_address("  21   Main Street  ") == "21 Main Street"
    with pytest.raises(XfinityError):
        clean_address("no")
    with pytest.raises(XfinityError):
        clean_address("21 Main\nStreet")


async def test_user_flow_shows_authentication_menu(hass, enable_custom_integrations):
    """Initial user step displays the authentication choice menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "menu"
    assert result["step_id"] == "user"
    assert set(result["menu_options"]) == {"browser", "browserless"}


async def test_user_flow_browser_success(hass, enable_custom_integrations):
    """User selects browser authentication, supplies address and callback URL."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    # Select browser authentication
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browser"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "browser"

    # Step 1: submit address
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: ADDRESS},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "browser_callback"
    assert "authorization_url" in result["description_placeholders"]
    auth_url = result["description_placeholders"]["authorization_url"]
    assert "https://xerxes-sub.xerxessecure.com" in auth_url

    # Step 2: submit callback URL
    tokens_payload = {
        "refresh_token": "browser-refresh-token",
        "access_token": "access",
    }
    with patch(
        "custom_components.xfinity.config_flow.async_complete_browser_auth",
        AsyncMock(return_value=tokens_payload),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"callback_url": CALLBACK_URL},
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_AUTH_METHOD] == AUTH_METHOD_BROWSER
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["data"][CONF_REFRESH_TOKEN] == "browser-refresh-token"
    assert CONF_PASSWORD not in result["data"]
    assert CONF_BROWSERLESS_API_KEY not in result["data"]


async def test_user_flow_browser_bad_address(hass, enable_custom_integrations):
    """User submits invalid address in browser step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browser"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: "no"},
    )
    assert result["errors"]["address"] == "invalid_address"


async def test_user_flow_browserless_stores_credentials_and_refresh_token(
    hass, enable_custom_integrations
):
    """User selects Browserless authentication and supplies credentials."""
    login = AsyncMock(
        return_value={
            "refresh_token": "rotated-refresh-token",
            "access_token": "access",
        }
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browserless"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "browserless"

    with patch("custom_components.xfinity.config_flow.async_browserless_login", login):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ADDRESS: ADDRESS,
                CONF_USERNAME: USERNAME,
                CONF_PASSWORD: PASSWORD,
                CONF_BROWSERLESS_API_KEY: API_KEY,
            },
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_AUTH_METHOD] == AUTH_METHOD_BROWSERLESS
    assert result["data"][CONF_REFRESH_TOKEN] == "rotated-refresh-token"
    assert result["data"][CONF_PASSWORD] == PASSWORD
    assert result["data"][CONF_BROWSERLESS_API_KEY] == API_KEY
    assert "access_token" not in result["data"]


async def test_user_flow_browserless_reports_errors(hass, enable_custom_integrations):
    """Browserless step handles login, MFA, and service errors."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browserless"},
    )
    form = {
        CONF_ADDRESS: ADDRESS,
        CONF_USERNAME: USERNAME,
        CONF_PASSWORD: PASSWORD,
        CONF_BROWSERLESS_API_KEY: API_KEY,
    }
    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(side_effect=XfinityLoginRejected("no")),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"]["base"] == "invalid_auth"

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(side_effect=XfinityMfaRequired("no")),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"]["base"] == "mfa_required"

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(side_effect=BrowserlessError("no")),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"]["base"] == "browserless_error"

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(side_effect=XfinityLoginError("no")),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"]["base"] == "cannot_connect"

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(return_value={"access_token": "a"}),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"]["base"] == "invalid_auth"


async def test_user_flow_browserless_rejects_bad_input(
    hass, enable_custom_integrations
):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browserless"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ADDRESS: "no",
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_BROWSERLESS_API_KEY: API_KEY,
        },
    )
    assert result["errors"]["address"] == "invalid_address"


async def test_browser_callback_error_handling(hass, enable_custom_integrations):
    """Test error handling in browser_callback step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browser"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: ADDRESS},
    )
    assert result["step_id"] == "browser_callback"

    # Invalid callback error keeps existing session active
    with patch(
        "custom_components.xfinity.config_flow.async_complete_browser_auth",
        AsyncMock(side_effect=XfinityInvalidCallbackError("Invalid")),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"callback_url": "bad_callback"},
        )
    assert result2["errors"]["base"] == "invalid_callback"

    # Expired session error creates fresh authorization session
    with patch(
        "custom_components.xfinity.config_flow.async_complete_browser_auth",
        AsyncMock(side_effect=XfinityBrowserSessionExpiredError("Expired")),
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"callback_url": CALLBACK_URL},
        )
    assert result3["errors"]["base"] == "session_expired"

    # Login rejected / invalid_auth creates fresh authorization session
    with patch(
        "custom_components.xfinity.config_flow.async_complete_browser_auth",
        AsyncMock(side_effect=XfinityLoginRejected("Rejected")),
    ):
        result4 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"callback_url": CALLBACK_URL},
        )
    assert result4["errors"]["base"] == "invalid_auth"

    # Connection error creates fresh authorization session
    with patch(
        "custom_components.xfinity.config_flow.async_complete_browser_auth",
        AsyncMock(side_effect=XfinityLoginError("Connection failed")),
    ):
        result5 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"callback_url": CALLBACK_URL},
        )
    assert result5["errors"]["base"] == "cannot_connect"


async def test_reauth_with_browser_handoff(hass, enable_custom_integrations):
    """Reauth flow can switch to or complete via browser handoff."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_AUTH_METHOD: AUTH_METHOD_BROWSERLESS,
            CONF_ADDRESS: ADDRESS,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: "old",
            CONF_BROWSERLESS_API_KEY: API_KEY,
            CONF_REFRESH_TOKEN: TOKEN,
        },
        version=2,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] == "menu"
    assert result["step_id"] == "reauth_confirm"

    # User chooses browser authentication
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browser"},
    )
    # Since address is already configured, advances straight to callback
    assert result["type"] == "form"
    assert result["step_id"] == "browser_callback"

    with patch(
        "custom_components.xfinity.config_flow.async_complete_browser_auth",
        AsyncMock(return_value={"refresh_token": "reauth-refresh-token"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"callback_url": CALLBACK_URL},
        )
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "reauth-refresh-token"
    assert entry.data[CONF_AUTH_METHOD] == AUTH_METHOD_BROWSER
    assert CONF_PASSWORD not in entry.data
    assert CONF_BROWSERLESS_API_KEY not in entry.data


async def test_reauth_with_browserless(hass, enable_custom_integrations):
    """Reauth flow can update Browserless credentials."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_AUTH_METHOD: AUTH_METHOD_BROWSERLESS,
            CONF_ADDRESS: ADDRESS,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: "old",
            CONF_BROWSERLESS_API_KEY: API_KEY,
            CONF_REFRESH_TOKEN: TOKEN,
        },
        version=2,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] == "menu"
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browserless"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "browserless"

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(return_value={"refresh_token": "new-refresh-token"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ADDRESS: ADDRESS,
                CONF_USERNAME: USERNAME,
                CONF_PASSWORD: PASSWORD,
                CONF_BROWSERLESS_API_KEY: API_KEY,
            },
        )
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "new-refresh-token"
    assert entry.data[CONF_PASSWORD] == PASSWORD


async def test_reconfigure_switch_to_browser(hass, enable_custom_integrations):
    """Reconfigure flow allows switching from Browserless to browser handoff."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_AUTH_METHOD: AUTH_METHOD_BROWSERLESS,
            CONF_ADDRESS: ADDRESS,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_BROWSERLESS_API_KEY: API_KEY,
            CONF_REFRESH_TOKEN: TOKEN,
        },
        version=2,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == "menu"
    assert result["step_id"] == "reconfigure"

    # Select browser authentication
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browser"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "browser"

    # Submit updated address
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: "400 Market Street"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "browser_callback"

    with patch(
        "custom_components.xfinity.config_flow.async_complete_browser_auth",
        AsyncMock(return_value={"refresh_token": "reconfig-browser-token"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"callback_url": CALLBACK_URL},
        )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_AUTH_METHOD] == AUTH_METHOD_BROWSER
    assert entry.data[CONF_ADDRESS] == "400 Market Street"
    assert entry.data[CONF_REFRESH_TOKEN] == "reconfig-browser-token"
    assert CONF_PASSWORD not in entry.data
    assert CONF_BROWSERLESS_API_KEY not in entry.data


async def test_reconfigure_with_browserless(hass, enable_custom_integrations):
    """Reconfigure flow allows updating Browserless settings."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_AUTH_METHOD: AUTH_METHOD_BROWSERLESS,
            CONF_ADDRESS: ADDRESS,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_BROWSERLESS_API_KEY: API_KEY,
            CONF_REFRESH_TOKEN: TOKEN,
        },
        version=2,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "browserless"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "browserless"

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(return_value={"refresh_token": "reconfig-key-token"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ADDRESS: "400 Market Street",
                CONF_USERNAME: USERNAME,
                CONF_PASSWORD: PASSWORD,
                CONF_BROWSERLESS_API_KEY: "replacement-api-key",
            },
        )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ADDRESS] == "400 Market Street"
    assert entry.data[CONF_BROWSERLESS_API_KEY] == "replacement-api-key"
    assert entry.data[CONF_REFRESH_TOKEN] == "reconfig-key-token"


async def test_reconfigure_update_service_address(hass, enable_custom_integrations):
    """Reconfigure flow allows updating only the service address without re-authenticating."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_AUTH_METHOD: AUTH_METHOD_BROWSER,
            CONF_ADDRESS: ADDRESS,
            CONF_REFRESH_TOKEN: TOKEN,
        },
        version=2,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == "menu"
    assert result["step_id"] == "reconfigure"
    assert "address" in result["menu_options"]

    # Select address update
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "address"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "address"

    # Submit bad address
    result_bad = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: "no"},
    )
    assert result_bad["errors"]["address"] == "invalid_address"

    # Submit valid address
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: "789 Pine Street"},
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ADDRESS] == "789 Pine Street"
    assert entry.data[CONF_AUTH_METHOD] == AUTH_METHOD_BROWSER
    assert entry.data[CONF_REFRESH_TOKEN] == TOKEN


async def test_second_entry_is_refused(hass, enable_custom_integrations):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN},
        version=2,
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "abort"
    assert result["reason"] in {"single_instance_allowed", "already_configured"}


async def test_version_one_entry_migrates(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN},
        version=1,
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.data[CONF_REFRESH_TOKEN] == TOKEN


def test_clean_helpers_and_input_error_mappings():
    assert clean_username("valid_user") == "valid_user"
    assert clean_password("valid_pass") == "valid_pass"
    assert clean_api_key("valid_api_key_12345") == "valid_api_key_12345"

    with pytest.raises(XfinityError):
        clean_username("ab")
    with pytest.raises(XfinityError):
        clean_username(123)
    with pytest.raises(XfinityError):
        clean_username("bad\x00user")

    with pytest.raises(XfinityError):
        clean_password("")
    with pytest.raises(XfinityError):
        clean_password(None)

    with pytest.raises(XfinityError):
        clean_api_key("short")
    with pytest.raises(XfinityError):
        clean_api_key(12345)

    with pytest.raises(XfinityError):
        clean_address(None)
    with pytest.raises(XfinityError):
        clean_address("a" * 201)

    assert _input_error(XfinityError("invalid address")) == {
        "address": "invalid_address"
    }
    assert _input_error(XfinityError("invalid username")) == {
        "username": "invalid_username"
    }
    assert _input_error(XfinityError("invalid password")) == {
        "password": "invalid_password"
    }
    assert _input_error(XfinityError("invalid browserless key")) == {
        "browserless_api_key": "invalid_browserless_key"
    }
    assert _input_error(XfinityError("unknown reason")) == {"base": "cannot_connect"}
