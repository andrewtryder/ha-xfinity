"""Diagnostics for Xfinity. Tokens and the service address are omitted."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS
from .coordinator import XfinityCoordinator

_SECTIONS = {"outage", "usage", "bill"}
_KEEP = {
    "active_outage",
    "allowance_gb",
    "amount_due",
    "autopay_date",
    "autopay_enabled",
    "billing_status",
    "classification",
    "customer_message",
    "cycle_end",
    "days_remaining",
    "due_date",
    "end_date",
    "estimated_restoration",
    "incident_count",
    "incidents",
    "last_payment_date",
    "new_charges",
    "past_due",
    "past_due_balance",
    "policy",
    "remaining_gb",
    "scheduled_maintenance",
    "start",
    "start_date",
    "status",
    "used_gb",
    "used_percent",
}


def _redact(value: Any, *, section: bool = False) -> Any:
    if isinstance(value, dict):
        allowed = _SECTIONS if section else _KEEP
        return {key: _redact(item) for key, item in value.items() if key in allowed}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict[str, Any]:
    """Return coordinator data without the address or any token."""
    coordinator: XfinityCoordinator = entry.runtime_data
    return {
        "address_configured": bool(entry.data.get(CONF_ADDRESS)),
        "data": _redact(coordinator.data, section=True),
    }
