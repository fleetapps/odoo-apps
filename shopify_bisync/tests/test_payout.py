# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Shopify Payments payout import, order->invoice matching, and payment
registration."""
from odoo.tests import tagged

from .common import ShopifyBisyncCase, order_payload


@tagged("post_install", "-at_install")
class TestPayout(ShopifyBisyncCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.make_product(cls, name="Payout Widget", sku="PO-1")
        cls.variant = cls.template.product_variant_id
        cls.bind(cls, cls.variant, 770001, inventory_item_id=440001)
        cls.PayoutSync = cls.env["shopify.bisync.payout.sync"]

    def _payout_dict(self):
        return {"id": 700001, "date": "2026-07-20", "status": "paid",
                "currency": "USD", "amount": "63.00",
                "summary": {"charges_gross_amount": "66.33",
                            "charges_fee_amount": "3.33"}}

    def test_01_import_matches_and_reconciles(self):
        journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)],
            limit=1)
        if not journal:
            self.skipTest("No accounting chart in this DB")
        self.instance.write({"confirm_policy": "confirm",
                             "payout_journal_id": journal.id})
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": self.variant.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "inventory_quantity": 10}).action_apply_inventory()
        self.OrderSync._import_order(
            self.instance, order_payload(id=990301, order_number=1301))
        so = self.env["sale.order"].search(
            [("client_order_ref", "=", "SHOPIFY/1301")])
        invoice = so._create_invoices()
        invoice.action_post()

        # Mock the balance-transactions REST endpoint.
        def fake_rest(instance, method, endpoint, payload=None, params=None):
            self.assertIn("balance/transactions", endpoint)
            return ({"transactions": [{
                "id": 900001, "type": "charge", "amount": "66.33",
                "fee": "3.33", "net": "63.00", "source_order_id": 990301}]}, {})
        self.patch_rest(fake_rest)

        self.PayoutSync._import_payout(self.instance, self._payout_dict())
        payout = self.env["shopify.bisync.payout"].search(
            [("shopify_payout_id", "=", "700001")])
        self.assertEqual(len(payout), 1)
        self.assertEqual(payout.matched_count, 1)
        txn = payout.transaction_ids
        self.assertEqual(txn.sale_order_id, so)
        self.assertEqual(txn.invoice_id, invoice)

        payout.action_register_payments()
        self.assertEqual(payout.state, "reconciled")
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))

    def test_02_no_shopify_payments_is_graceful(self):
        """Payout list 404 (store without Shopify Payments) must not raise."""
        from odoo.exceptions import UserError

        def fake_call(*args, **kwargs):
            raise UserError("Shopify REST 404 on shopify_payments/payouts.json")
        import unittest.mock as mock
        with mock.patch.object(self.instance_cls, "api_call",
                               side_effect=fake_call, autospec=True):
            self.instance.import_payouts = True
            # Must simply skip, not raise.
            self.env["shopify.bisync.payout.cron"].cron_import_payouts()
        self.assertFalse(self.env["shopify.bisync.payout"].search_count([]))
