"""Config flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xfinity import async_migrate_entry
from custom_components.xfinity.api import XfinityError
from custom_components.xfinity.config_flow import clean_address
from custom_components.xfinity.const import (
    CONF_ADDRESS,
    CONF_BROWSERLESS_API_KEY,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.xfinity.login import BrowserlessError, XfinityLoginRejected, XfinityMfaRequired

ADDRESS = "21 Main Street"
USERNAME = "user@example.com"
PASSWORD = "correct-password"
API_KEY = "browserless-api-key"
TOKEN = "refresh-token-value"


def test_input_validation():
    assert clean_address("  21   Main Street  ") == "21 Main Street"
    with pytest.raises(XfinityError):
        clean_address("no")
    with pytest.raises(XfinityError):
        clean_address("21 Main\nStreet")


async def test_user_flow_stores_credentials_and_refresh_token(hass, enable_custom_integrations):
    login = AsyncMock(return_value={"refresh_token": "rotated-refresh-token", "access_token": "access"})
    with patch("custom_components.xfinity.config_flow.async_browserless_login", login):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ADDRESS: ADDRESS, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD, CONF_BROWSERLESS_API_KEY: API_KEY},
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_REFRESH_TOKEN] == "rotated-refresh-token"
    assert result["data"][CONF_PASSWORD] == PASSWORD
    assert "access_token" not in result["data"]


async def test_user_flow_reports_auth_mfa_and_browserless_errors(hass, enable_custom_integrations):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    form = {
        CONF_ADDRESS: ADDRESS,
        CONF_USERNAME: USERNAME,
        CONF_PASSWORD: PASSWORD,
        CONF_BROWSERLESS_API_KEY: API_KEY,
    }
    with patch("custom_components.xfinity.config_flow.async_browserless_login", AsyncMock(side_effect=XfinityLoginRejected("no"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"]["base"] == "invalid_auth"
    with patch("custom_components.xfinity.config_flow.async_browserless_login", AsyncMock(side_effect=XfinityMfaRequired("no"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"]["base"] == "mfa_required"
    with patch("custom_components.xfinity.config_flow.async_browserless_login", AsyncMock(side_effect=BrowserlessError("no"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"]["base"] == "browserless_error"


async def test_user_flow_rejects_bad_input(hass, enable_custom_integrations):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: "no", CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD, CONF_BROWSERLESS_API_KEY: API_KEY},
    )
    assert result["errors"]["address"] == "invalid_address"


async def test_reauth_and_reconfigure(hass, enable_custom_integrations):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_USERNAME: USERNAME, CONF_PASSWORD: "old", CONF_BROWSERLESS_API_KEY: API_KEY, CONF_REFRESH_TOKEN: TOKEN},
        version=2,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.xfinity.config_flow.async_browserless_login", AsyncMock(return_value={"refresh_token": "rotated-refresh-token"})):
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
        {CONF_ADDRESS: "400 Market Street", CONF_BROWSERLESS_API_KEY: "replacement-api-key"},
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ADDRESS] == "400 Market Street"
    assert entry.data[CONF_BROWSERLESS_API_KEY] == "replacement-api-key"


async def test_second_entry_is_refused(hass, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN}, version=2)
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "abort"
    assert result["reason"] in {"single_instance_allowed", "already_configured"}


async def test_version_one_entry_migrates(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN}, version=1)
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.data[CONF_REFRESH_TOKEN] == TOKEN
