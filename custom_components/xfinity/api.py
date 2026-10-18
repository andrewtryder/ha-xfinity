"""HTTP client and response parsing aligned with xfinity_api_probe.py."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timezone
import json
import time

from .const import (
    AUTH_TOKEN_URL,
    BILL_OPERATION,
    BILL_QUERY,
    CLIENT_ID,
    CLIENT_SECRET,
    GRAPHQL_URL,
    MAX_RESPONSE_BYTES,
    OUTAGE_SESSION_URL,
    OUTAGE_STATUS_URL,
    USAGE_OPERATION,
    USAGE_OPERATION_ID,
    USAGE_QUERY,
)

COMMON_WEB_HEADERS = {
    "Origin": "https://www.xfinity.com",
    "Referer": "https://www.xfinity.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
}
_MOBILE_HEADERS = {
    "User-Agent": "Digital Home / Samsung SM-G991B / Android 14",
    "client": "digital-home-android",
    "client-detail": "MOBILE;Samsung;SM-G991B;Android 14;v5.38.0",
    "accept-language": "en-US",
    "Content-Type": "application/json",
    "Accept": "multipart/mixed; deferSpec=20220824, application/json",
}
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class XfinityError(Exception):
    """API failure that should be retried later."""


class XfinityConnectionError(XfinityError):
    """The network request failed or the response could not be read."""


class XfinityAuthError(XfinityError):
    """The refresh token was rejected."""


class XfinityClientRejected(XfinityError):
    """Xfinity rejected the bundled application credential."""


def as_iso(value):
    if isinstance(value, dict):
        return value.get("iso")
    return value if isinstance(value, str) else None


def iso_to_datetime(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def outage_summary(data: dict, now=None) -> dict:
    """Classify planned records as scheduled or underway maintenance."""
    if "outageInfoResponseList" not in data:
        return {"status": "unknown", "note": "No outageInfoResponseList key"}
    outages = data.get("outageInfoResponseList")
    if not isinstance(outages, list):
        return {"status": "unknown", "note": "Outage list is not a list"}
    if not outages:
        return {"status": "no_reported_incident", "active_outage": False, "scheduled_maintenance": False, "incidents": []}
    message = data.get("customerMessage")
    if not isinstance(message, dict):
        return {"status": "unknown", "incident_count": len(outages), "scheduled_maintenance": False}
    now = now or datetime.now(timezone.utc)
    summaries = []
    for index, outage in enumerate(outages):
        if not isinstance(outage, dict):
            summaries.append({"classification": "unknown"})
            continue
        planned = message.get("planned")
        start = as_iso(outage.get("startTime"))
        start_dt = iso_to_datetime(start)
        upcoming = bool(start_dt and start_dt.tzinfo and start_dt > now)
        if planned is True and upcoming:
            classification = "scheduled_maintenance"
        elif planned is True and start_dt and start_dt.tzinfo:
            classification = "planned_maintenance_underway"
        elif planned is False:
            classification = "unplanned_outage"
        else:
            classification = "unknown"
        details = message.get("messages")
        summaries.append({
            "index": index,
            "classification": classification,
            "outage_id": outage.get("outageId"),
            "planned": planned,
            "outage_type": message.get("outageType"),
            "affected_services": outage.get("services"),
            "start": start,
            "estimated_restoration": as_iso(message.get("etr")),
            "estimated_restoration_display": message.get("etrOutputString"),
            "summary": details.get("shortDescription") if isinstance(details, dict) else None,
            "customer_message": message.get("customerFacingText"),
        })
    return {
        "status": "unknown" if any(item["classification"] == "unknown" for item in summaries) else "incidents_reported",
        "active_outage": any(item["classification"] in ("planned_maintenance_underway", "unplanned_outage") for item in summaries),
        "scheduled_maintenance": any(item["classification"] == "scheduled_maintenance" for item in summaries),
        "incident_count": len(outages),
        "incidents": summaries,
    }


def to_gb(quantity) -> float | None:
    if not isinstance(quantity, dict):
        return None
    try:
        value = float(quantity["value"])
    except (KeyError, TypeError, ValueError):
        return None
    if value < 0:
        return None
    unit = str(quantity.get("unit", "")).upper()
    if unit == "TB":
        return round(value * 1000, 2)
    if unit == "GB":
        return round(value, 2)
    if unit == "MB":
        return round(value / 1000, 2)
    return None


def usage_summary(data: dict, today=None) -> dict:
    """Current-cycle usage. The caller supplies the local date."""
    if data.get("errors"):
        raise XfinityError("GraphQL reported errors")
    try:
        months = data["data"]["accountByServiceAccountId"]["internet"]["usage"]["monthlyUsage"]
    except (KeyError, TypeError) as err:
        raise XfinityError("Usage response missing expected fields") from err
    if not isinstance(months, list):
        raise XfinityError("Usage history is not a list")
    if not months:
        return {"status": "no_usage_history"}
    today = today or date.today()
    valid = []
    for month in months:
        try:
            start = date.fromisoformat(month["startDate"])
            end = date.fromisoformat(month["endDate"])
            if start <= end and start <= today:
                valid.append((start, end, month))
        except (KeyError, TypeError, ValueError):
            continue
    current = [item for item in valid if item[0] <= today <= item[1]]
    choices = current or valid
    if not choices:
        raise XfinityError("No readable current or historical usage cycle")
    latest = max(choices, key=lambda item: item[0])[2]
    used = to_gb(latest.get("currentUsage"))
    allowed = to_gb(latest.get("allowableUsage"))
    if str(latest.get("policy", "")).lower() == "unlimited":
        allowed = None
    return {
        "status": ("ok" if used is not None else "incomplete_usage") if current else "historical_only",
        "start_date": latest.get("startDate"),
        "end_date": latest.get("endDate"),
        "days_remaining": latest.get("daysRemaining"),
        "policy": latest.get("policy"),
        "used_gb": used,
        "allowance_gb": allowed,
        "remaining_gb": round(max(0, allowed - used), 2) if used is not None and allowed is not None else None,
        "used_percent": round(100 * used / allowed, 1) if used is not None and allowed and allowed > 0 else None,
    }


def _money(quantity) -> float | None:
    if not isinstance(quantity, dict):
        return None
    try:
        value = round(float(quantity["value"]), 2)
    except (KeyError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def bill_summary(data: dict) -> dict:
    """Bill status from the account landing card. Account numbers are omitted."""
    if data.get("errors"):
        raise XfinityError("GraphQL reported errors")
    try:
        account = data["data"]["accountByServiceAccountId"]["postPaidBillingAccount"]["coreBillingAccount"]["account"]
    except (KeyError, TypeError) as err:
        raise XfinityError("Bill response missing expected fields") from err
    if not isinstance(account, dict):
        raise XfinityError("Bill account was not an object")
    past_due = _money(account.get("pastDueAmount"))
    cycle = account.get("currentBillingCycle")
    autopay = account.get("autoPay") if isinstance(account.get("autoPay"), dict) else {}
    status = account.get("billingAccountStatus")
    return {
        "status": "ok" if isinstance(status, str) and status else "unknown",
        "billing_status": status,
        "amount_due": _money(account.get("billDueAmount")),
        "due_date": account.get("billDueDate"),
        "past_due": past_due,
        "past_due_balance": bool(past_due and past_due > 0),
        "new_charges": _money(account.get("newCharges")),
        "last_payment_date": account.get("lastPaymentProcessedDate"),
        "cycle_end": cycle.get("endDate") if isinstance(cycle, dict) else None,
        "autopay_enabled": bool(autopay.get("isEnabled")),
        "autopay_date": autopay.get("scheduleDate"),
    }


def _json_bytes(body: bytes):
    if not body:
        raise XfinityConnectionError("response was not JSON")
    try:
        return json.loads(body)
    except ValueError as err:
        raise XfinityConnectionError("response was not JSON") from err


class XfinityClient:
    """Poll outage anonymously and account data with a stored refresh token."""

    def __init__(
        self,
        session,
        address: str,
        refresh_token: str,
        *,
        on_tokens_rotated: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self.address = address
        self.refresh_token = refresh_token
        self.access_token = ""
        self.id_token = ""
        self.expires_at = 0.0
        self._on_tokens_rotated = on_tokens_rotated
        self._refresh_lock = asyncio.Lock()

    async def _send(self, method: str, url: str, **kwargs) -> tuple[int, dict[str, str], bytes]:
        import aiohttp

        try:
            async with self._session.request(
                method,
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                allow_redirects=False,
                **kwargs,
            ) as response:
                body = await response.read()
                status = response.status
                headers = {key: value for key, value in response.headers.items()}
        except (aiohttp.ClientError, TimeoutError) as err:
            raise XfinityConnectionError("network request failed") from err
        if len(body) > MAX_RESPONSE_BYTES:
            raise XfinityConnectionError("response too large")
        if status in _RETRYABLE_STATUS:
            raise XfinityConnectionError(f"temporary HTTP {status}")
        return status, headers, body

    async def async_refresh(self) -> None:
        """Exchange the refresh token. Raises when Xfinity rejects it."""
        async with self._refresh_lock:
            await self._refresh_locked()

    async def async_ensure_token(self, *, force: bool = False) -> None:
        """Refresh when the access token is missing, expired, or forced."""
        async with self._refresh_lock:
            if not force and self.access_token and self.id_token and self.expires_at > time.time() + 120:
                return
            await self._refresh_locked()

    async def _refresh_locked(self) -> None:
        status, _, body = await self._send(
            "POST",
            AUTH_TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "active_x1_account_count": "true",
                "partner_id": "comcast",
                "mso_partner_hint": "true",
                "scope": "profile",
                "rm_hint": "true",
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_secret": CLIENT_SECRET,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; SM-G991B)",
            },
        )
        payload = _json_bytes(body) if body else {}
        if not isinstance(payload, dict):
            raise XfinityAuthError("refresh rejected")
        if status in (400, 401) or payload.get("error"):
            if payload.get("error") == "invalid_client":
                raise XfinityClientRejected("app credential rejected")
            raise XfinityAuthError("refresh rejected")
        if status < 200 or status >= 300:
            raise XfinityConnectionError(f"token endpoint returned HTTP {status}")
        access = payload.get("access_token")
        identity = payload.get("id_token")
        if not isinstance(access, str) or not access or not isinstance(identity, str) or not identity:
            raise XfinityAuthError("refresh rejected")
        previous = self.refresh_token
        self.access_token = access
        self.id_token = identity
        rotated = payload.get("refresh_token")
        if isinstance(rotated, str) and rotated:
            self.refresh_token = rotated
        try:
            lifetime = float(payload["expires_in"])
            self.expires_at = time.time() + lifetime if lifetime > 0 else 0
        except (KeyError, TypeError, ValueError):
            self.expires_at = 0
        if self.refresh_token != previous and self._on_tokens_rotated is not None:
            self._on_tokens_rotated(self.refresh_token)

    async def async_fetch_outage(self) -> dict:
        """Return the address outage summary. This call does not use the account token."""
        status, headers, _ = await self._send(
            "POST",
            OUTAGE_SESSION_URL,
            headers={**COMMON_WEB_HEADERS, "Content-Length": "0"},
            data=b"",
        )
        if status != 201:
            raise XfinityError(f"outage session returned HTTP {status}")
        token = headers.get("X-OutageDataService-Token")
        if not token:
            raise XfinityError("outage session missing token")
        status, _, body = await self._send(
            "GET",
            OUTAGE_STATUS_URL,
            params={"includeIdealOutage": "true", "tokenizeDateTime": "true", "address": self.address},
            headers={**COMMON_WEB_HEADERS, "Authorization": f"Bearer {token}"},
        )
        if status < 200 or status >= 300:
            raise XfinityError(f"outage status returned HTTP {status}")
        payload = _json_bytes(body)
        if not isinstance(payload, dict):
            raise XfinityError("outage status was not an object")
        summary = outage_summary(payload)
        summary.setdefault("scheduled_maintenance", False)
        summary.setdefault("active_outage", False)
        return summary

    async def async_fetch_usage(self, today: date | None = None) -> dict:
        """Return internet usage for the cycle containing today."""
        payload = await self._graphql(USAGE_OPERATION, USAGE_QUERY, operation_id=USAGE_OPERATION_ID)
        return usage_summary(payload, today)

    async def async_fetch_bill(self) -> dict:
        """Return the current bill card. The account number is not included."""
        payload = await self._graphql(BILL_OPERATION, BILL_QUERY)
        return bill_summary(payload)

    async def _graphql(self, operation: str, query: str, *, operation_id: str | None = None) -> dict:
        await self.async_ensure_token()
        try:
            return await self._graphql_request(operation, query, operation_id)
        except XfinityAuthError:
            await self.async_ensure_token(force=True)
            return await self._graphql_request(operation, query, operation_id)

    async def _graphql_request(self, operation: str, query: str, operation_id: str | None) -> dict:
        headers = dict(_MOBILE_HEADERS)
        headers["Authorization"] = f"Bearer {self.access_token}"
        headers["x-id-token"] = self.id_token
        headers["x-apollo-operation-name"] = operation
        if operation_id:
            headers["x-apollo-operation-id"] = operation_id
        status, _, body = await self._send(
            "POST",
            GRAPHQL_URL,
            headers=headers,
            json={"operationName": operation, "variables": {}, "query": query},
        )
        if status == 401:
            raise XfinityAuthError("access token rejected")
        if status < 200 or status >= 300:
            raise XfinityError(f"{operation} returned HTTP {status}")
        payload = _json_bytes(body)
        if not isinstance(payload, dict):
            raise XfinityError("response was not an object")
        return payload
