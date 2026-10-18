"""Offline checks for the integration API helpers."""

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from custom_components.xfinity.api import bill_summary, outage_summary, usage_summary
from custom_components.xfinity.const import ACCOUNT_OUTAGE_SUMMARY_URLS


NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def outage(planned, start):
    return {
        "outageInfoResponseList": [{"outageId": "1", "startTime": start, "services": ["internet"]}],
        "customerMessage": {"planned": planned, "outageType": "HSD", "messages": {"shortDescription": "work"}, "customerFacingText": "planned work"},
    }


class ApiHelperTests(unittest.TestCase):
    def test_scheduled_maintenance(self):
        payload = outage(True, "2026-10-20T00:00:00+00:00")
        integration = outage_summary(payload, NOW)
        self.assertEqual(integration["incidents"][0]["classification"], "scheduled_maintenance")
        self.assertFalse(integration["active_outage"])
        self.assertTrue(integration["scheduled_maintenance"])

    def test_underway_maintenance(self):
        payload = outage(True, "2026-10-18T00:00:00+00:00")
        integration = outage_summary(payload, NOW)
        self.assertEqual(integration["incidents"][0]["classification"], "planned_maintenance_underway")
        self.assertTrue(integration["active_outage"])
        self.assertFalse(integration["scheduled_maintenance"])

    def test_bill_summary_omits_account_number(self):
        result = bill_summary({"data": {"accountByServiceAccountId": {"postPaidBillingAccount": {"coreBillingAccount": {"account": {
            "billingAccountStatus": "BALANCE_DUE",
            "accountNumber": "secret",
            "billDueAmount": {"value": "10.50", "unit": "USD"},
            "billDueDate": "2026-11-08",
            "pastDueAmount": {"value": "0.00", "unit": "USD"},
            "autoPay": {"isEnabled": True, "scheduleDate": "2026-11-08"},
            "currentBillingCycle": {"endDate": "2026-11-19"},
        }}}}}})
        self.assertEqual(result["billing_status"], "BALANCE_DUE")
        self.assertEqual(result["amount_due"], 10.5)
        self.assertFalse(result["past_due_balance"])
        self.assertNotIn("accountNumber", result)
        self.assertNotIn("secret", json.dumps(result))

    def test_usage_summary(self):
        result = usage_summary({"data": {"accountByServiceAccountId": {"internet": {"usage": {"monthlyUsage": [{
            "startDate": "2026-10-01", "endDate": "2026-10-31", "policy": "unlimited",
            "currentUsage": {"value": 2000, "unit": "GB"}, "allowableUsage": {"value": 1.2, "unit": "TB"},
        }]}}}}}, datetime(2026, 10, 18).date())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["used_gb"], 2000)
        self.assertIsNone(result["allowance_gb"])

    def test_account_outage_routes_are_recorded_not_called(self):
        self.assertEqual(len(ACCOUNT_OUTAGE_SUMMARY_URLS), 2)
        self.assertTrue(all("{xbo_account_id}" in url for url in ACCOUNT_OUTAGE_SUMMARY_URLS))
        source = Path("custom_components/xfinity/api.py").read_text()
        self.assertNotIn("outage-experience", source)
