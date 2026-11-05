"""Tests for Xfinity diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.xfinity.const import (
    CONF_ADDRESS,
    CONF_BROWSERLESS_API_KEY,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from custom_components.xfinity.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redacts_credentials_and_pii(hass) -> None:
    """Verify that credentials, tokens, addresses, and sensitive fields are excluded."""
    entry = MagicMock()
    entry.data = {
        CONF_USERNAME: "sensitive_user@example.com",
        CONF_PASSWORD: "super_secret_password_123",
        CONF_BROWSERLESS_API_KEY: "secret_browserless_key_xyz",
        CONF_ADDRESS: "123 Elm Street, Cityville, ST 12345",
        "access_token": "bearer_jwt_token_do_not_leak",
        "refresh_token": "refresh_token_do_not_leak",
    }

    coordinator = MagicMock()
    coordinator.data = {
        "usage": {
            "used_gb": 450.5,
            "allowance_gb": 1200.0,
            "used_percent": 37.5,
            "remaining_gb": 749.5,
            "days_remaining": 15,
            "policy": "standard",
            # Sensitive fields that must be redacted:
            "account_number": "999888777666",
            "customer_name": "John Doe",
            "billing_address": "123 Elm Street",
            "token": "leaked_token",
        },
        "outage": {
            "status": "incidents_reported",
            "active_outage": True,
            "scheduled_maintenance": False,
            "incident_count": 1,
            "incidents": [
                {
                    "classification": "unplanned_outage",
                    "start": "2026-11-05T10:00:00Z",
                    "estimated_restoration": "2026-11-05T14:00:00Z",
                    "customer_message": "Service interruption detected",
                    # Sensitive fields inside incidents that must be redacted:
                    "node_id": "NODE_XYZ_INTERNAL",
                    "latitude": 40.7128,
                    "longitude": -74.0060,
                }
            ],
        },
        "bill": {
            "amount_due": 85.50,
            "due_date": "2026-11-20",
            "autopay_enabled": True,
            "billing_status": "current",
            # Sensitive fields that must be redacted:
            "bank_account": "****1234",
            "credit_card": "****5678",
        },
        # Unapproved sections that must be completely dropped:
        "auth": {
            "refresh_token": "leak_me_not",
            "cookies": {"session": "abc123xyz"},
        },
        "account_info": {
            "ssn_last4": "1234",
            "phone": "555-123-4567",
        },
    }
    entry.runtime_data = coordinator

    diag = await async_get_config_entry_diagnostics(hass, entry)

    # Boolean flags report presence without leaking values
    assert diag["address_configured"] is True
    assert diag["credentials_configured"] is True

    # Confirm sensitive values never appear anywhere in the serialized diagnostics
    dump = str(diag)
    assert "sensitive_user@example.com" not in dump
    assert "super_secret_password_123" not in dump
    assert "secret_browserless_key_xyz" not in dump
    assert "123 Elm Street" not in dump
    assert "bearer_jwt_token_do_not_leak" not in dump
    assert "999888777666" not in dump
    assert "John Doe" not in dump
    assert "NODE_XYZ_INTERNAL" not in dump
    assert "40.7128" not in dump
    assert "****1234" not in dump
    assert "leak_me_not" not in dump
    assert "555-123-4567" not in dump

    # Approved allowed fields are retained
    data = diag["data"]
    assert "usage" in data
    assert data["usage"]["used_gb"] == 450.5
    assert data["usage"]["allowance_gb"] == 1200.0
    assert "account_number" not in data["usage"]

    assert "outage" in data
    assert data["outage"]["active_outage"] is True
    assert len(data["outage"]["incidents"]) == 1
    assert data["outage"]["incidents"][0]["classification"] == "unplanned_outage"
    assert "node_id" not in data["outage"]["incidents"][0]

    assert "bill" in data
    assert data["bill"]["amount_due"] == 85.50
    assert "bank_account" not in data["bill"]

    # Disallowed sections are excluded
    assert "auth" not in data
    assert "account_info" not in data


async def test_diagnostics_unconfigured_credentials(hass) -> None:
    """Verify boolean flags when credentials or address are missing."""
    entry = MagicMock()
    entry.data = {}
    coordinator = MagicMock()
    coordinator.data = {}
    entry.runtime_data = coordinator

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["address_configured"] is False
    assert diag["credentials_configured"] is False
    assert diag["data"] == {}
