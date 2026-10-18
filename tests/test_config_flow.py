"""Config flow tests."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xfinity.config_flow import clean_address, clean_token
from custom_components.xfinity.const import AUTH_TOKEN_URL, CONF_ADDRESS, CONF_REFRESH_TOKEN, DOMAIN
from custom_components.xfinity.api import XfinityError

ADDRESS = "21 Main Street"
TOKEN = "refresh-token-value"
OK = {"access_token": "access", "id_token": "identity", "expires_in": 3600, "refresh_token": "rotated-refresh-token"}


def test_input_validation():
    assert clean_address("  21   Main Street  ") == "21 Main Street"
    with pytest.raises(XfinityError):
        clean_address("no")
    with pytest.raises(XfinityError):
        clean_address("21 Main\nStreet")
    assert clean_token(f"  {TOKEN}  ") == TOKEN
    with pytest.raises(XfinityError):
        clean_token("has space token")
    with pytest.raises(XfinityError):
        clean_token("short")


async def test_user_flow_stores_only_the_refresh_token(hass, aioclient_mock, enable_custom_integrations):
    aioclient_mock.post(AUTH_TOKEN_URL, json=OK)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN},
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: "rotated-refresh-token"}


async def test_user_flow_reports_auth_and_connection_errors(hass, aioclient_mock, enable_custom_integrations):
    aioclient_mock.post(AUTH_TOKEN_URL, status=400, json={"error": "invalid_grant"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN},
    )
    assert result["errors"]["base"] == "invalid_auth"

    aioclient_mock.clear_requests()
    aioclient_mock.post(AUTH_TOKEN_URL, status=400, json={"error": "invalid_client"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN},
    )
    assert result["errors"]["base"] == "invalid_client"

    aioclient_mock.clear_requests()
    aioclient_mock.post(AUTH_TOKEN_URL, status=503, json={})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN},
    )
    assert result["errors"]["base"] == "cannot_connect"


async def test_user_flow_rejects_bad_input(hass, enable_custom_integrations):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: "no", CONF_REFRESH_TOKEN: "has space token"},
    )
    assert result["type"] == "form"
    assert result["errors"]["address"] == "invalid_address"


async def test_reauth_and_reconfigure(hass, aioclient_mock, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    aioclient_mock.post(AUTH_TOKEN_URL, json=OK)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REFRESH_TOKEN: TOKEN})
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated-refresh-token"

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: "400 Market Street"})
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ADDRESS] == "400 Market Street"


async def test_second_entry_is_refused(hass, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "abort"
    assert result["reason"] in {"single_instance_allowed", "already_configured"}
