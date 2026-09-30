# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Setup warnings and the invoice policy.

Both replace something that used to be discovered the expensive way — a wrong
total on an imported order, or invoices that simply never appeared — so both
are worth a test that fails loudly if the condition stops being detected.
"""
from odoo.tests import tagged

from .common import ShopifyBisyncCase


@tagged("post_install", "-at_install")
class TestSetupWarnings(ShopifyBisyncCase):

    def setUp(self):
        super().setUp()
        # A connected store: the warnings deliberately stay silent before that,
        # because the form is already telling them to press Connect.
        self.instance.write({
            "access_token": "shpat_test",
            "webhooks_registered_on": "2026-08-04 10:00:00",
            "fallback_product_id": self.env["product.product"].create(
                {"name": "Unmatched Shopify line"}).id,
        })

    def test_01_silent_when_nothing_is_wrong(self):
        self.instance.write({"sync_stock": "off", "shop_currency": False})
        self.assertFalse(self.instance.setup_warnings)

    def test_02_missing_pricelist_for_the_store_currency_is_flagged(self):
        """The KES case: knowable at setup, not one order at a time."""
        self.instance.write({"sync_orders": "import", "shop_currency": "XTS"})
        self.assertIn("XTS", self.instance.setup_warnings or "")

    def test_03_missing_fallback_product_is_flagged(self):
        self.instance.write({"sync_orders": "import",
                             "fallback_product_id": False})
        self.assertIn("fallback", (self.instance.setup_warnings or "").lower())

    def test_04_unmapped_locations_flagged_only_when_exporting_stock(self):
        self.instance.write({"sync_stock": "export"})
        self.assertIn("warehouse", (self.instance.setup_warnings or "").lower())
        self.instance.write({"sync_stock": "import"})
        self.assertNotIn("warehouse",
                         (self.instance.setup_warnings or "").lower())

    def test_05_webhooks_not_registered_is_flagged(self):
        self.instance.webhooks_registered_on = False
        self.assertIn("Live updates", self.instance.setup_warnings or "")


@tagged("post_install", "-at_install")
class TestInvoicePolicy(ShopifyBisyncCase):

    def _due(self, policy, financial=None, fulfillment=None):
        self.instance.invoice_policy = policy
        return self.env["shopify.bisync.order.sync"]._invoice_due_now(
            self.instance,
            {"financial_status": financial, "fulfillment_status": fulfillment})

    def test_01_all_invoices_regardless(self):
        self.assertTrue(self._due("all", financial="pending"))

    def test_02_paid_waits_for_payment(self):
        self.assertFalse(self._due("paid", financial="pending"))
        self.assertTrue(self._due("paid", financial="paid"))

    def test_03_fulfilled_waits_for_full_dispatch(self):
        self.assertFalse(self._due("fulfilled", fulfillment=None))
        self.assertFalse(self._due("fulfilled", fulfillment="partial"),
                         "a half-shipped order is not a completed sale")
        self.assertTrue(self._due("fulfilled", fulfillment="fulfilled"))
