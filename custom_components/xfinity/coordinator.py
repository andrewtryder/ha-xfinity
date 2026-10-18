"""Data update coordinator for Xfinity."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any, TypedDict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import XfinityAuthError, XfinityClient, XfinityClientRejected, XfinityError
from .const import (
    CONF_ADDRESS,
    CONF_BROWSERLESS_API_KEY,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
    INVALID_CLIENT_ISSUE,
    OUTAGE_INTERVAL,
    RELOGIN_INTERVAL_SECONDS,
    USAGE_INTERVAL,
)
from .login import BrowserlessError, XfinityLoginError, async_browserless_login

_LOGGER = logging.getLogger(__name__)


class XfinityData(TypedDict):
    """Latest outage, usage, and bill sections."""

    outage: dict[str, Any]
    usage: dict[str, Any]
    bill: dict[str, Any]


class XfinityCoordinator(DataUpdateCoordinator[XfinityData]):
    """Poll outage every 30 minutes and account data every six hours."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=OUTAGE_INTERVAL,
            config_entry=entry,
        )
        self.entry = entry
        self.client = XfinityClient(
            async_get_clientsession(hass),
            entry.data[CONF_ADDRESS],
            entry.data[CONF_REFRESH_TOKEN],
            on_tokens_rotated=self._persist_refresh_token,
        )
        self._account_due = datetime.now(timezone.utc)
        self._warned: set[str] = set()
        self._last_login: float | None = None

    async def _async_setup(self) -> None:
        """Confirm the entry can be polled before the first refresh."""
        if not self.entry.data.get(CONF_ADDRESS) or not self.entry.data.get(CONF_REFRESH_TOKEN):
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth")

    async def _async_update_data(self) -> XfinityData:
        outage = await self._outage()
        usage, bill = await self._account()
        sections = (outage, usage, bill)
        if self.data is None and all(section.get("status") == "error" for section in sections):
            raise ConfigEntryNotReady(translation_domain=DOMAIN, translation_key="not_ready")
        return {"outage": outage, "usage": usage, "bill": bill}

    async def _outage(self) -> dict[str, Any]:
        try:
            result = await self.client.async_fetch_outage()
        except XfinityError as err:
            return self._failed("outage", (self.data or {}).get("outage"), err)
        return self._recovered("outage", result)

    async def _account(self) -> tuple[dict[str, Any], dict[str, Any]]:
        now = datetime.now(timezone.utc)
        previous_usage = (self.data or {}).get("usage")
        previous_bill = (self.data or {}).get("bill")
        usage_fresh = isinstance(previous_usage, dict) and previous_usage.get("status") not in (None, "error")
        bill_fresh = isinstance(previous_bill, dict) and previous_bill.get("status") not in (None, "error")
        if usage_fresh and bill_fresh and now < self._account_due:
            return previous_usage, previous_bill
        client_rejected = False
        try:
            usage = await self.client.async_fetch_usage(dt_util.now().date())
        except XfinityClientRejected as err:
            client_rejected = True
            usage = self._failed("usage", previous_usage, err)
        except XfinityAuthError:
            if not await self._relogin():
                raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth") from None
            usage = await self._usage_after_login(previous_usage)
        except XfinityError as err:
            usage = self._failed("usage", previous_usage, err)
        else:
            usage = self._recovered("usage", usage)
        try:
            bill = await self.client.async_fetch_bill()
        except XfinityClientRejected as err:
            client_rejected = True
            bill = self._failed("bill", previous_bill, err)
        except XfinityAuthError:
            if not await self._relogin():
                raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth") from None
            bill = await self._bill_after_login(previous_bill)
        except XfinityError as err:
            bill = self._failed("bill", previous_bill, err)
        else:
            bill = self._recovered("bill", bill)
        if client_rejected:
            self._create_client_issue()
        elif usage.get("status") != "error" and bill.get("status") != "error":
            self._clear_client_issue()
            self._account_due = now + USAGE_INTERVAL
        return usage, bill

    async def _usage_after_login(self, previous: Any) -> dict[str, Any]:
        try:
            return self._recovered("usage", await self.client.async_fetch_usage(dt_util.now().date()))
        except XfinityAuthError:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth") from None
        except XfinityError as err:
            return self._failed("usage", previous, err)

    async def _bill_after_login(self, previous: Any) -> dict[str, Any]:
        try:
            return self._recovered("bill", await self.client.async_fetch_bill())
        except XfinityAuthError:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth") from None
        except XfinityError as err:
            return self._failed("bill", previous, err)

    async def _relogin(self) -> bool:
        """Sign in again at most once an hour. Returns whether a new token was stored."""
        now = time.monotonic()
        if self._last_login is not None and now - self._last_login < RELOGIN_INTERVAL_SECONDS:
            return False
        self._last_login = now
        data = self.entry.data
        username = data.get(CONF_USERNAME)
        password = data.get(CONF_PASSWORD)
        api_key = data.get(CONF_BROWSERLESS_API_KEY)
        if not username or not password or not api_key:
            return False
        try:
            tokens = await async_browserless_login(async_get_clientsession(self.hass), username, password, api_key)
        except (XfinityLoginError, BrowserlessError) as err:
            _LOGGER.warning("Automatic Xfinity sign-in failed: %s", type(err).__name__)
            return False
        refresh = tokens.get("refresh_token")
        access = tokens.get("access_token")
        identity = tokens.get("id_token")
        if not isinstance(refresh, str) or not isinstance(access, str) or not isinstance(identity, str):
            return False
        self.client.refresh_token = refresh
        self.client.access_token = access
        self.client.id_token = identity
        try:
            lifetime = float(tokens["expires_in"])
            self.client.expires_at = time.time() + lifetime if lifetime > 0 else 0
        except (KeyError, TypeError, ValueError):
            self.client.expires_at = 0
        self._persist_refresh_token(refresh)
        return True

    def _failed(self, name: str, previous: Any, err: Exception) -> dict[str, Any]:
        if name not in self._warned:
            _LOGGER.warning("%s poll failed: %s", name, err)
            self._warned.add(name)
        else:
            _LOGGER.debug("%s poll failed: %s", name, err)
        if isinstance(previous, dict) and previous.get("status") not in (None, "error"):
            return previous
        fallback: dict[str, Any] = {"status": "error"}
        if name == "outage":
            fallback.update(active_outage=False, scheduled_maintenance=False, incidents=[])
        if name == "bill":
            fallback["past_due_balance"] = False
        return fallback

    def _recovered(self, name: str, result: dict[str, Any]) -> dict[str, Any]:
        if name in self._warned:
            _LOGGER.info("%s poll recovered", name)
            self._warned.discard(name)
        return result

    def _persist_refresh_token(self, refresh_token: str) -> None:
        """Store a rotated refresh token immediately. Access tokens stay in memory."""
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_REFRESH_TOKEN: refresh_token},
        )

    def _create_client_issue(self) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            INVALID_CLIENT_ISSUE,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=INVALID_CLIENT_ISSUE,
        )

    def _clear_client_issue(self) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, INVALID_CLIENT_ISSUE)
