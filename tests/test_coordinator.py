"""Config entry setup, coordinator failures, and diagnostics."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xfinity.api import XfinityAuthError, XfinityClientRejected, XfinityConnectionError, XfinityError
from custom_components.xfinity.const import CONF_ADDRESS, CONF_REFRESH_TOKEN, DOMAIN, INVALID_CLIENT_ISSUE
from custom_components.xfinity.coordinator import XfinityCoordinator
from custom_components.xfinity.diagnostics import async_get_config_entry_diagnostics
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir

ADDRESS = "21 Main Street"
TOKEN = "refresh-token-value"


def _entry(hass, **data):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN, **data})
    entry.add_to_hass(hass)
    return entry


class _Client:
    def __init__(self, session, address, refresh_token, *, on_tokens_rotated=None):
        self.address = address
        self.refresh_token = refresh_token
        self.on_tokens_rotated = on_tokens_rotated

    async def async_fetch_outage(self):
        return {"status": "no_reported_incident", "active_outage": False, "scheduled_maintenance": False, "incidents": []}

    async def async_fetch_usage(self, today=None):
        return {"status": "ok", "used_gb": 1, "end_date": "2026-10-31"}

    async def async_fetch_bill(self):
        return {"status": "ok", "billing_status": "BALANCE_DUE", "past_due_balance": False, "due_date": "2026-11-08"}


async def test_setup_drops_stored_access_tokens(hass, enable_custom_integrations):
    entry = _entry(hass, access_token="access", id_token="identity", expires_at=1, client_secret="secret")
    with patch("custom_components.xfinity.coordinator.XfinityClient", _Client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert "access_token" not in entry.data
    assert "id_token" not in entry.data
    assert entry.data[CONF_REFRESH_TOKEN] == TOKEN
    state = hass.states.get("sensor.xfinity_data_used")
    assert state is not None
    assert state.state == "1"


async def test_rotated_refresh_token_is_stored(hass):
    entry = _entry(hass)

    class Rotating(_Client):
        async def async_fetch_usage(self, today=None):
            self.on_tokens_rotated("rotated-refresh-token")
            return await super().async_fetch_usage(today)

    with patch("custom_components.xfinity.coordinator.XfinityClient", Rotating):
        coordinator = XfinityCoordinator(hass, entry)
        await coordinator._async_update_data()
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated-refresh-token"
    assert "access_token" not in entry.data


async def test_rejected_refresh_token_starts_reauth(hass):
    entry = _entry(hass)

    class Rejected(_Client):
        async def async_fetch_usage(self, today=None):
            raise XfinityAuthError("refresh rejected")

    with patch("custom_components.xfinity.coordinator.XfinityClient", Rejected):
        coordinator = XfinityCoordinator(hass, entry)
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()


async def test_http_403_does_not_start_reauth(hass):
    entry = _entry(hass)

    class Blocked(_Client):
        async def async_fetch_usage(self, today=None):
            raise XfinityError("InternetDataUsage returned HTTP 403")

    with patch("custom_components.xfinity.coordinator.XfinityClient", Blocked):
        coordinator = XfinityCoordinator(hass, entry)
        data = await coordinator._async_update_data()
    assert data["usage"]["status"] == "error"
    assert data["outage"]["status"] == "no_reported_incident"


async def test_timeout_keeps_the_other_sections(hass):
    entry = _entry(hass)

    class TimedOut(_Client):
        async def async_fetch_outage(self):
            raise XfinityConnectionError("network request failed")

    with patch("custom_components.xfinity.coordinator.XfinityClient", TimedOut):
        coordinator = XfinityCoordinator(hass, entry)
        data = await coordinator._async_update_data()
    assert data["outage"]["status"] == "error"
    assert data["bill"]["status"] == "ok"


async def test_invalid_client_opens_a_repair_issue(hass):
    entry = _entry(hass)

    class RejectedApp(_Client):
        async def async_fetch_usage(self, today=None):
            raise XfinityClientRejected("app credential rejected")

        async def async_fetch_bill(self):
            raise XfinityClientRejected("app credential rejected")

    with patch("custom_components.xfinity.coordinator.XfinityClient", RejectedApp):
        coordinator = XfinityCoordinator(hass, entry)
        data = await coordinator._async_update_data()
    assert data["usage"]["status"] == "error"
    assert ir.async_get(hass).async_get_issue(DOMAIN, INVALID_CLIENT_ISSUE) is not None


async def test_diagnostics_redact_address_and_tokens(hass, enable_custom_integrations):
    entry = _entry(hass)
    with patch("custom_components.xfinity.coordinator.XfinityClient", _Client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    result = await async_get_config_entry_diagnostics(hass, entry)
    text = str(result)
    assert TOKEN not in text
    assert ADDRESS not in text
    assert result["address_configured"] is True
    assert result["data"]["usage"]["used_gb"] == 1
