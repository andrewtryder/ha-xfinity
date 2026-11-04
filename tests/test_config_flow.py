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
    CONF_ADDRESS,
    CONF_BROWSERLESS_API_KEY,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.xfinity.login import (
    BrowserlessError,
    XfinityLoginError,
    XfinityLoginRejected,
    XfinityMfaRequired,
)

ADDRESS = "21 Main Street"
USERNAME = "user@example.com"
PASSWORD = "correct-password"
API_KEY = "browserless-api-key"
TOKEN = "refresh-token-value"
INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "xfinity"


def test_config_fields_have_translated_descriptions():
    """Every displayed config field explains the value expected from the user."""
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    translation = json.loads((INTEGRATION_DIR / "translations" / "en.json").read_text())
    assert translation == strings

    for step in strings["config"]["step"].values():
        assert set(step["data_description"]) == set(step["data"])


def test_input_validation():
    assert clean_address("  21   Main Street  ") == "21 Main Street"
    with pytest.raises(XfinityError):
        clean_address("no")
    with pytest.raises(XfinityError):
        clean_address("21 Main\nStreet")


async def test_user_flow_stores_credentials_and_refresh_token(
    hass, enable_custom_integrations
):
    login = AsyncMock(
        return_value={
            "refresh_token": "rotated-refresh-token",
            "access_token": "access",
        }
    )
    with patch("custom_components.xfinity.config_flow.async_browserless_login", login):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
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
    assert result["data"][CONF_REFRESH_TOKEN] == "rotated-refresh-token"
    assert result["data"][CONF_PASSWORD] == PASSWORD
    assert "access_token" not in result["data"]


async def test_user_flow_reports_auth_mfa_and_browserless_errors(
    hass, enable_custom_integrations
):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
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


async def test_user_flow_rejects_bad_input(hass, enable_custom_integrations):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
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


async def test_reauth_and_reconfigure(hass, enable_custom_integrations):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: "old",
            CONF_BROWSERLESS_API_KEY: API_KEY,
            CONF_REFRESH_TOKEN: TOKEN,
        },
        version=2,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(return_value={"refresh_token": "rotated-refresh-token"}),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: PASSWORD, CONF_BROWSERLESS_API_KEY: API_KEY},
        )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated-refresh-token"
    assert entry.data[CONF_PASSWORD] == PASSWORD

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ADDRESS: "400 Market Street",
            CONF_BROWSERLESS_API_KEY: "replacement-api-key",
        },
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ADDRESS] == "400 Market Street"
    assert entry.data[CONF_BROWSERLESS_API_KEY] == "replacement-api-key"


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


async def test_reauth_error_branches(hass, enable_custom_integrations):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: "old",
            CONF_BROWSERLESS_API_KEY: API_KEY,
            CONF_REFRESH_TOKEN: TOKEN,
        },
        version=2,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(side_effect=XfinityMfaRequired("mfa")),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: PASSWORD, CONF_BROWSERLESS_API_KEY: API_KEY},
        )
    assert result["errors"]["base"] == "mfa_required"

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(side_effect=XfinityLoginRejected("rejected")),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: PASSWORD, CONF_BROWSERLESS_API_KEY: API_KEY},
        )
    assert result["errors"]["base"] == "invalid_auth"

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(side_effect=BrowserlessError("err")),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: PASSWORD, CONF_BROWSERLESS_API_KEY: API_KEY},
        )
    assert result["errors"]["base"] == "browserless_error"

    with patch(
        "custom_components.xfinity.config_flow.async_browserless_login",
        AsyncMock(side_effect=XfinityLoginError("err")),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: PASSWORD, CONF_BROWSERLESS_API_KEY: API_KEY},
        )
    assert result["errors"]["base"] == "cannot_connect"

    # Invalid input during reauth
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: "", CONF_BROWSERLESS_API_KEY: API_KEY},
    )
    assert result["errors"]["password"] == "invalid_password"

    # Direct call to async_step_reauth
    from custom_components.xfinity.config_flow import XfinityConfigFlow

    flow = XfinityConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id, "source": "reauth"}
    res = await flow.async_step_reauth({})
    assert res["type"] == "form"
    assert res["step_id"] == "reauth_confirm"


async def test_reconfigure_error_branches(hass, enable_custom_integrations):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
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
        {CONF_ADDRESS: "no", CONF_BROWSERLESS_API_KEY: API_KEY},
    )
    assert result["errors"]["address"] == "invalid_address"

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: ADDRESS, CONF_BROWSERLESS_API_KEY: "short"},
    )
    assert result["errors"]["browserless_api_key"] == "invalid_browserless_key"
