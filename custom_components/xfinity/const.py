"""Constants for the Xfinity integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "xfinity"

CONF_ADDRESS = "address"
CONF_REFRESH_TOKEN = "refresh_token"

# Keys written by 0.1.0. Removed from the config entry on setup.
STALE_ENTRY_KEYS = ("access_token", "id_token", "expires_at", "client_secret", "token_file")

OUTAGE_STATUS_OPTIONS = ("no_reported_incident", "incidents_reported", "unknown")

MAX_RESPONSE_BYTES = 1_048_576
TOKEN_MIN_LENGTH = 16
TOKEN_MAX_LENGTH = 4096
ADDRESS_MIN_LENGTH = 5
ADDRESS_MAX_LENGTH = 200

OUTAGE_INTERVAL = timedelta(minutes=30)
USAGE_INTERVAL = timedelta(hours=6)

OUTAGE_SESSION_URL = "https://api.sc.xfinity.com/outagedata/session"
OUTAGE_STATUS_URL = "https://api.sc.xfinity.com/outage/consolidated/lob/v3"
AUTH_TOKEN_URL = "https://xerxes-sub.xerxessecure.com/xerxes-ctrl/oauth/token"
GRAPHQL_URL = "https://gw.api.dh.comcast.com/galileo/graphql"
CLIENT_ID = "xfinity-android-application"
# Public OAuth credential shipped inside the official Xfinity Android app.
CLIENT_SECRET = "f40e1d73c8ed4b4c82e2fd48ebc23f7a"
USAGE_OPERATION_ID = "61994c6016ac8c0ebcca875084919e5e01cb3b116a86aaf9646e597c3a1fbd06"
USAGE_OPERATION = "InternetDataUsage"
BILL_OPERATION = "AccountLandingCoreBillingCardV2"
CONFIGURATION_URL = "https://customer.xfinity.com"
INVALID_CLIENT_ISSUE = "invalid_client"

# Discovered in the Xfinity Android app (DefaultOutageService.getOutages).
# Not polled: both routes returned HTTP 404 "no Route matched".
ACCOUNT_OUTAGE_SUMMARY_URLS = (
    "https://gw.api.dh.comcast.com/outage-experience/api/outage_summary/accounts/{xbo_account_id}/enhanced",
    "https://gw.api.dh.comcast.com/outage-experience/api/outage_summary/accounts/{xbo_account_id}/detailed",
)

BILL_QUERY = """query AccountLandingCoreBillingCardV2 {
  accountByServiceAccountId {
    postPaidBillingAccount {
      coreBillingAccount {
        account {
          billingAccountStatus
          billDueAmount { value unit }
          billDueDate
          currentBillingCycle { endDate }
          lastPaymentProcessedDate
          pastDueAmount { value unit }
          newCharges { value unit }
          softDisconnectedDate
          autoPay { isEnabled scheduleDate }
        }
      }
    }
  }
}"""

USAGE_QUERY = """query InternetDataUsage {
  accountByServiceAccountId {
    internet {
      usage {
        monthlyUsage {
          policy
          startDate
          endDate
          daysRemaining
          currentUsage { value unit }
          allowableUsage { value unit }
        }
      }
    }
  }
}"""
