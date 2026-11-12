"""Config entry setup, coordinator failures, and diagnostics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xfinity.api import (
    XfinityAuthError,
    XfinityClientRejected,
    XfinityConnectionError,
    XfinityError,
)
from custom_components.xfinity.const import (
    AUTH_METHOD_BROWSER,
    AUTH_METHOD_BROWSERLESS,
    CONF_ADDRESS,
    CONF_AUTH_METHOD,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    INVALID_CLIENT_ISSUE,
)
from custom_components.xfinity.coordinator import XfinityCoordinator
from custom_components.xfinity.diagnostics import async_get_config_entry_diagnostics
from custom_components.xfinity.login import BrowserlessError

ADDRESS = "21 Main Street"
TOKEN = "refresh-token-value"


def _entry(hass, **data):
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_ADDRESS: ADDRESS, CONF_REFRESH_TOKEN: TOKEN, **data}
    )
    entry.add_to_hass(hass)
    return entry


class _Client:
    def __init__(self, session, address, refresh_token, *, on_tokens_rotated=None):
        self.address = address
        self.refresh_token = refresh_token
        self.on_tokens_rotated = on_tokens_rotated

    async def async_fetch_outage(self):
        return {
            "status": "no_reported_incident",
            "active_outage": False,
            "scheduled_maintenance": False,
            "incidents": [],
        }

    async def async_fetch_usage(self, today=None):
        return {"status": "ok", "used_gb": 1, "end_date": "2026-10-31"}

    async def async_fetch_bill(self):
        return {
            "status": "ok",
            "billing_status": "BALANCE_DUE",
            "past_due_balance": False,
            "due_date": "2026-11-08",
        }


async def test_setup_drops_stored_access_tokens(hass, enable_custom_integrations):
    entry = _entry(
        hass,
        access_token="access",
        id_token="identity",
        expires_at=1,
        client_secret="secret",
    )
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
    entry = _entry(
        hass,
        username="user@example.com",
        password="super-secret-password",
        browserless_api_key="browserless-api-key",
    )
    with patch("custom_components.xfinity.coordinator.XfinityClient", _Client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    result = await async_get_config_entry_diagnostics(hass, entry)
    text = str(result)
    assert TOKEN not in text
    assert ADDRESS not in text
    assert "super-secret-password" not in text
    assert "browserless-api-key" not in text
    assert result["address_configured"] is True
    assert result["credentials_configured"] is True
    assert result["data"]["usage"]["used_gb"] == 1


async def test_rejected_token_signs_in_again(hass):
    entry = _entry(
        hass,
        username="user@example.com",
        password="secret",
        browserless_api_key="browserless-api-key",
    )
    calls = {"usage": 0}

    class Expired(_Client):
        async def async_fetch_usage(self, today=None):
            calls["usage"] += 1
            if calls["usage"] == 1:
                raise XfinityAuthError("refresh rejected")
            return await super().async_fetch_usage(today)

    login = AsyncMock(
        return_value={
            "refresh_token": "rotated-refresh-token",
            "access_token": "access",
            "id_token": "identity",
            "expires_in": 3600,
        }
    )
    with (
        patch("custom_components.xfinity.coordinator.XfinityClient", Expired),
        patch("custom_components.xfinity.coordinator.async_browserless_login", login),
    ):
        coordinator = XfinityCoordinator(hass, entry)
        data = await coordinator._async_update_data()
    assert data["usage"]["status"] == "ok"
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated-refresh-token"
    login.assert_awaited_once()


async def test_relogin_is_limited_to_once_an_hour(hass):
    entry = _entry(
        hass,
        username="user@example.com",
        password="secret",
        browserless_api_key="browserless-api-key",
    )

    class Expired(_Client):
        async def async_fetch_usage(self, today=None):
            raise XfinityAuthError("refresh rejected")

    login = AsyncMock(
        return_value={
            "refresh_token": "rotated-refresh-token",
            "access_token": "access",
            "id_token": "identity",
            "expires_in": 3600,
        }
    )
    with (
        patch("custom_components.xfinity.coordinator.XfinityClient", Expired),
        patch("custom_components.xfinity.coordinator.async_browserless_login", login),
    ):
        coordinator = XfinityCoordinator(hass, entry)
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
    login.assert_awaited_once()


async def test_relogin_failure_modes(hass):
    entry_no_creds = _entry(hass)
    coordinator = XfinityCoordinator(hass, entry_no_creds)
    assert await coordinator._relogin() is False

    entry_browser = _entry(
        hass,
        auth_method=AUTH_METHOD_BROWSER,
        username="u",
        password="p",
        browserless_api_key="k",
    )
    coordinator_browser = XfinityCoordinator(hass, entry_browser)
    assert await coordinator_browser._relogin() is False

    entry_with_creds = _entry(hass, username="u", password="p", browserless_api_key="k")
    coordinator2 = XfinityCoordinator(hass, entry_with_creds)
    with patch(
        "custom_components.xfinity.coordinator.async_browserless_login",
        AsyncMock(side_effect=BrowserlessError("err")),
    ):
        assert await coordinator2._relogin() is False

    coordinator3 = XfinityCoordinator(hass, entry_with_creds)
    with patch(
        "custom_components.xfinity.coordinator.async_browserless_login",
        AsyncMock(return_value={"refresh_token": "r"}),
    ):
        assert await coordinator3._relogin() is False

    coordinator4 = XfinityCoordinator(hass, entry_with_creds)
    with patch(
        "custom_components.xfinity.coordinator.async_browserless_login",
        AsyncMock(
            return_value={
                "refresh_token": "r",
                "access_token": "a",
                "id_token": "i",
                "expires_in": "not-a-number",
            }
        ),
    ):
        assert await coordinator4._relogin() is True
        assert coordinator4.client.expires_at == 0


async def test_bill_relogin_and_after_login_paths(hass):
    entry = _entry(
        hass, username="user@example.com", password="secret", browserless_api_key="key"
    )
    calls = {"bill": 0}

    class BillExpired(_Client):
        async def async_fetch_bill(self):
            calls["bill"] += 1
            if calls["bill"] == 1:
                raise XfinityAuthError("bill auth failed")
            return {"status": "ok", "billing_status": "PAID"}

    login = AsyncMock(
        return_value={
            "refresh_token": "r",
            "access_token": "a",
            "id_token": "i",
            "expires_in": 3600,
        }
    )
    with (
        patch("custom_components.xfinity.coordinator.XfinityClient", BillExpired),
        patch("custom_components.xfinity.coordinator.async_browserless_login", login),
    ):
        coordinator = XfinityCoordinator(hass, entry)
        data = await coordinator._async_update_data()
        assert data["bill"]["status"] == "ok"

    class BillStillAuthFails(_Client):
        async def async_fetch_bill(self):
            raise XfinityAuthError("bill auth failed again")

    with (
        patch(
            "custom_components.xfinity.coordinator.XfinityClient", BillStillAuthFails
        ),
        patch("custom_components.xfinity.coordinator.async_browserless_login", login),
    ):
        coordinator = XfinityCoordinator(hass, entry)
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    class BillFailsOther(_Client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.bill_calls = 0

        async def async_fetch_bill(self):
            self.bill_calls += 1
            if self.bill_calls == 1:
                raise XfinityAuthError("auth")
            raise XfinityError("other")

    with (
        patch("custom_components.xfinity.coordinator.XfinityClient", BillFailsOther),
        patch("custom_components.xfinity.coordinator.async_browserless_login", login),
    ):
        coordinator = XfinityCoordinator(hass, entry)
        data = await coordinator._async_update_data()
        assert data["bill"]["status"] == "error"


async def test_usage_after_login_paths(hass):
    entry = _entry(
        hass, username="user@example.com", password="secret", browserless_api_key="key"
    )
    login = AsyncMock(
        return_value={
            "refresh_token": "r",
            "access_token": "a",
            "id_token": "i",
            "expires_in": 3600,
        }
    )

    class UsageFailsOther(_Client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.usage_calls = 0

        async def async_fetch_usage(self, today=None):
            self.usage_calls += 1
            if self.usage_calls == 1:
                raise XfinityAuthError("auth")
            raise XfinityError("other")

    with (
        patch("custom_components.xfinity.coordinator.XfinityClient", UsageFailsOther),
        patch("custom_components.xfinity.coordinator.async_browserless_login", login),
    ):
        coordinator = XfinityCoordinator(hass, entry)
        data = await coordinator._async_update_data()
        assert data["usage"]["status"] == "error"


async def test_coordinator_failed_and_recovery_flow(hass):
    entry = _entry(hass)

    class FailingOutage(_Client):
        async def async_fetch_outage(self):
            raise XfinityError("outage error")

    with patch("custom_components.xfinity.coordinator.XfinityClient", FailingOutage):
        coordinator = XfinityCoordinator(hass, entry)
        data1 = await coordinator._async_update_data()
        assert "outage" in coordinator._warned
        assert data1["outage"]["status"] == "error"

        data2 = await coordinator._async_update_data()
        assert data2["outage"]["status"] == "error"

    coordinator.client = _Client(None, ADDRESS, TOKEN)
    data3 = await coordinator._async_update_data()
    assert "outage" not in coordinator._warned
    assert data3["outage"]["status"] == "no_reported_incident"


def test_sensor_helpers():
    from custom_components.xfinity.sensor import _as_date, _outage_status

    parse_date = _as_date("due_date")
    assert parse_date({"due_date": "not-a-date"}) is None
    assert parse_date({"due_date": "2026-11-08"}) is not None
    assert parse_date({"due_date": 12345}) is None
    assert parse_date({}) is None

    assert _outage_status({"status": "unknown_value"}) == "unknown"


async def test_outage_sensor_extra_state_attributes(hass, enable_custom_integrations):
    class OutageClient(_Client):
        async def async_fetch_outage(self):
            return {
                "status": "reported_outage",
                "active_outage": True,
                "scheduled_maintenance": False,
                "incidents": [
                    {
                        "classification": "planned",
                        "start": "2026-11-04T12:00:00Z",
                        "estimated_restoration": "2026-11-04T16:00:00Z",
                        "customer_message": "Maintenance in progress",
                    }
                ],
            }

    entry = _entry(hass)
    with patch("custom_components.xfinity.coordinator.XfinityClient", OutageClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.xfinity_outage_status")
    assert state is not None
    assert state.attributes["classification"] == "planned"
    assert state.attributes["customer_message"] == "Maintenance in progress"


async def test_diagnostic_timestamp_sensors(hass, enable_custom_integrations):
    entry = _entry(hass)
    with patch("custom_components.xfinity.coordinator.XfinityClient", _Client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    outage_sensor = hass.states.get("sensor.xfinity_last_successful_outage_check")
    assert outage_sensor is not None
    assert outage_sensor.state != "unknown"
    assert outage_sensor.attributes.get("device_class") == "timestamp"

    account_sensor = hass.states.get("sensor.xfinity_last_successful_account_update")
    assert account_sensor is not None
    assert account_sensor.state != "unknown"
    assert account_sensor.attributes.get("device_class") == "timestamp"


async def test_diagnostic_timestamps_on_failure_and_cache(
    hass, enable_custom_integrations
):
    entry = _entry(hass)
    with patch("custom_components.xfinity.coordinator.XfinityClient", _Client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator: XfinityCoordinator = entry.runtime_data
    initial_outage_time = coordinator.last_successful_outage_check
    initial_account_time = coordinator.last_successful_account_update
    assert initial_outage_time is not None
    assert initial_account_time is not None

    # On a subsequent poll where outage fails and account is cached:
    class FailingOutageClient(_Client):
        async def async_fetch_outage(self):
            raise XfinityError("Outage endpoint down")

    coordinator.client = FailingOutageClient(None, ADDRESS, TOKEN)
    await coordinator.async_refresh()

    # Outage failed: last successful timestamp must be retained, not cleared
    assert coordinator.last_successful_outage_check == initial_outage_time
    # Account was cached: timestamp must remain unchanged
    assert coordinator.last_successful_account_update == initial_account_time


async def test_structured_logging(hass, enable_custom_integrations, caplog):
    caplog.set_level(logging.DEBUG)
    entry = _entry(hass)
    with patch("custom_components.xfinity.coordinator.XfinityClient", _Client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Verify INFO and DEBUG messages during initial poll and setup
    messages = [record.message for record in caplog.records]
    assert any("Xfinity integration started successfully" in m for m in messages)
    assert any("Starting Xfinity outage check" in m for m in messages)
    assert any("Xfinity outage check completed in" in m for m in messages)
    assert any("Starting Xfinity account check" in m for m in messages)
    assert any("Xfinity usage check completed in" in m for m in messages)
    assert any("Xfinity billing check completed in" in m for m in messages)
    assert any("Xfinity account check completed in" in m for m in messages)

    # Verify cached poll logs debug message about skipping
    coordinator: XfinityCoordinator = entry.runtime_data
    await coordinator.async_refresh()
    messages_after = [record.message for record in caplog.records]
    assert any(
        "Skipping account check: cached usage and billing are fresh until" in m
        for m in messages_after
    )

    # Verify token rotation log
    coordinator._persist_refresh_token("new-token-123")
    assert any(
        "Xfinity OAuth refresh token rotated and saved" in record.message
        for record in caplog.records
    )

    # Verify unload log
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert any(
        "Xfinity integration unloaded" in record.message for record in caplog.records
    )


async def test_account_update_not_advanced_when_usage_fails(hass):
    """When live usage fails but billing succeeds with valid previous data, do not advance account update."""
    entry = _entry(hass)
    with patch("custom_components.xfinity.coordinator.XfinityClient", _Client):
        coordinator = XfinityCoordinator(hass, entry)
        await coordinator.async_refresh()

    initial_time = coordinator.last_successful_account_update
    assert initial_time is not None

    # Simulate due time reached
    past_due = datetime.now(timezone.utc) - timedelta(seconds=1)
    coordinator._account_due = past_due

    class UsageFailsBillSucceeds(_Client):
        async def async_fetch_usage(self, today=None):
            raise XfinityError("Usage API temporary error")

        async def async_fetch_bill(self):
            return {
                "status": "ok",
                "billing_status": "PAID",
                "amount_due": 0.0,
            }

    coordinator.client = UsageFailsBillSucceeds(None, ADDRESS, TOKEN)
    await coordinator.async_refresh()
    data = coordinator.data

    # Previous usage data preserved for entities
    assert data["usage"]["status"] == "ok"
    # New billing data received
    assert data["bill"]["billing_status"] == "PAID"

    # Crucial assertion: timestamp and schedule deadline are NOT advanced
    assert coordinator.last_successful_account_update == initial_time
    assert coordinator._account_due == past_due


async def test_account_update_not_advanced_when_billing_fails(hass):
    """When live billing fails but usage succeeds with valid previous data, do not advance account update."""
    entry = _entry(hass)
    with patch("custom_components.xfinity.coordinator.XfinityClient", _Client):
        coordinator = XfinityCoordinator(hass, entry)
        await coordinator.async_refresh()

    initial_time = coordinator.last_successful_account_update
    assert initial_time is not None

    past_due = datetime.now(timezone.utc) - timedelta(seconds=1)
    coordinator._account_due = past_due

    class UsageSucceedsBillFails(_Client):
        async def async_fetch_usage(self, today=None):
            return {"status": "ok", "used_gb": 99}

        async def async_fetch_bill(self):
            raise XfinityError("Billing API temporary error")

    coordinator.client = UsageSucceedsBillFails(None, ADDRESS, TOKEN)
    await coordinator.async_refresh()
    data = coordinator.data

    # New usage data received
    assert data["usage"]["used_gb"] == 99
    # Previous bill data preserved for entities
    assert data["bill"]["billing_status"] == "BALANCE_DUE"

    # Crucial assertion: timestamp and schedule deadline are NOT advanced
    assert coordinator.last_successful_account_update == initial_time
    assert coordinator._account_due == past_due


async def test_auth_expired_logging_distinguishes_method(hass, caplog):
    """Verify warning wording indicates user reauthentication for browser vs automatic for browserless."""
    caplog.set_level(logging.WARNING)

    # 1. Browser method
    browser_entry = _entry(hass, **{CONF_AUTH_METHOD: AUTH_METHOD_BROWSER})

    class AuthExpiredClient(_Client):
        async def async_fetch_usage(self, today=None):
            raise XfinityAuthError("expired")

    with patch(
        "custom_components.xfinity.coordinator.XfinityClient", AuthExpiredClient
    ):
        coordinator = XfinityCoordinator(hass, browser_entry)
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    assert any(
        "Xfinity authorization expired during usage check; user reauthentication required"
        in record.message
        for record in caplog.records
    )

    caplog.clear()

    # 2. Browserless method
    browserless_entry = _entry(
        hass,
        **{
            CONF_AUTH_METHOD: AUTH_METHOD_BROWSERLESS,
            "username": "user@example.com",
            "password": "secret_password",
            "browserless_api_key": "api_key_123",
        },
    )

    login = AsyncMock(
        return_value={
            "refresh_token": "new-r",
            "access_token": "new-a",
            "id_token": "new-i",
            "expires_in": 3600,
        }
    )

    with (
        patch(
            "custom_components.xfinity.coordinator.XfinityClient",
            AuthExpiredClient,
        ),
        patch(
            "custom_components.xfinity.coordinator.async_browserless_login",
            login,
        ),
    ):
        coordinator = XfinityCoordinator(hass, browserless_entry)
        # First call raises auth, relogin succeeds, second call in _usage_after_login also raises AuthExpiredClient -> ConfigEntryAuthFailed
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    assert any(
        "Xfinity authorization expired during usage check; attempting automatic Browserless sign-in"
        in record.message
        for record in caplog.records
    )
