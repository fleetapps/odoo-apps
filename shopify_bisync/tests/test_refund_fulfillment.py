# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""A1/A7: refunds -> draft credit note (never posted); fulfillment export
through the FulfillmentOrders API with partial shipments (2 pickings -> 2
fulfillments with tracking)."""
from odoo.tests import tagged

from .common import ShopifyBisyncCase, order_payload


@tagged("post_install", "-at_install")
class TestRefundFulfillment(ShopifyBisyncCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.make_product(cls, name="Ship Me", sku="SHIP-1")
        cls.variant = cls.template.product_variant_id
        cls.bind(cls, cls.variant, 770001, inventory_item_id=440001)
        cls.RefundSync = cls.env["shopify.bisync.refund.sync"]
        cls.FulfillmentSync = cls.env["shopify.bisync.fulfillment.sync"]

    def _need_accounting(self):
        journal = self.env["account.journal"].search(
            [("type", "=", "sale"), ("company_id", "=", self.company.id)],
            limit=1)
        if not journal:
            self.skipTest("No sale journal / chart of accounts in this DB")

    # ---------------------------------------------------------------- refund
    def test_01_partial_refund_draft_credit_note(self):
        self._need_accounting()
        self.instance.write({"tax_policy": "shopify",
                             "confirm_policy": "draft"})
        self.OrderSync._import_order(self.instance, order_payload())
        so = self.env["sale.order"].search(
            [("client_order_ref", "=", "SHOPIFY/1001")])
        so.action_confirm()
        invoice = so._create_invoices()
        invoice.action_post()
        refund = {
            "id": 31337, "order_id": 990001,
            "refund_line_items": [{
                "line_item_id": 880001, "quantity": 1,
                "subtotal": "18.32", "total_tax": "1.29",
                "line_item": {"title": "Widget", "sku": "WID-1"},
            }],
            "order_adjustments": [{
                "kind": "shipping_refund", "amount": "-2.00"}],
        }
        self.RefundSync._import_refund(self.instance, refund)
        credit = self.env["account.move"].search(
            [("move_type", "=", "out_refund"),
             ("ref", "like", "31337")])
        self.assertEqual(len(credit), 1)
        self.assertEqual(credit.state, "draft", "refunds are NEVER auto-posted")
        self.assertEqual(credit.reversed_entry_id, invoice)
        product_line = credit.invoice_line_ids.filtered(
            lambda l: l.product_id == self.variant)
        self.assertEqual((product_line.quantity,
                          f"{product_line.price_unit:.2f}"), (1.0, "18.32"))
        # tax adjustment 1.29 + shipping refund 2.00 on the adjustment product
        adjustment_total = sum(credit.invoice_line_ids.filtered(
            lambda l: l.product_id == self.instance.adjustment_product_id
        ).mapped("price_unit"))
        self.assertEqual(f"{adjustment_total:.2f}", "3.29")
        self.assertEqual(f"{credit.amount_total:.2f}", "21.61")
        # Redelivery of the same refund webhook: idempotent.
        self.RefundSync._import_refund(self.instance, refund)
        self.assertEqual(self.env["account.move"].search_count(
            [("move_type", "=", "out_refund"), ("ref", "like", "31337")]), 1)

    def test_02_refund_without_invoice_logs_mismatch(self):
        self.instance.confirm_policy = "draft"
        self.OrderSync._import_order(
            self.instance, order_payload(id=990030, order_number=1030))
        self.RefundSync._import_refund(self.instance, {
            "id": 31338, "order_id": 990030,
            "refund_line_items": [{"line_item_id": 880001, "quantity": 1,
                                   "subtotal": "5.00", "total_tax": "0"}]})
        self.assertFalse(self.env["account.move"].search_count(
            [("move_type", "=", "out_refund"), ("ref", "like", "31338")]))
        self.assertTrue(self.env["shopify.bisync.mismatch"].search_count(
            [("kind", "=", "refund_no_invoice")]))

    # ----------------------------------------------------------- fulfillment
    def test_03_partial_fulfillments_two_pickings(self):
        self.instance.confirm_policy = "confirm"
        # 10 on hand so both pickings can reserve.
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": self.variant.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "inventory_quantity": 10}).action_apply_inventory()
        payload = order_payload(id=990040, order_number=1040)
        payload["line_items"][0]["quantity"] = 5
        self.OrderSync._import_order(self.instance, payload)
        so = self.env["sale.order"].search(
            [("client_order_ref", "=", "SHOPIFY/1040")])
        self.assertEqual(so.state, "sale")
        picking = so.picking_ids
        self.assertEqual(len(picking), 1)

        remaining = {"qty": 5}
        calls = {"created": []}

        def fake_graphql(instance, query, variables=None):
            if "fulfillmentOrders" in query:
                return {"order": {"fulfillmentOrders": {"nodes": [{
                    "id": "gid://shopify/FulfillmentOrder/1",
                    "status": "OPEN",
                    "lineItems": {"nodes": [{
                        "id": "gid://shopify/FulfillmentOrderLineItem/11",
                        "remainingQuantity": remaining["qty"],
                        "lineItem": {"id": "gid://shopify/LineItem/880001",
                                     "sku": "WID-1"}}]},
                }]}}}
            fo = variables["fulfillment"]["lineItemsByFulfillmentOrder"][0]
            quantity = fo["fulfillmentOrderLineItems"][0]["quantity"]
            calls["created"].append(
                (quantity,
                 (variables["fulfillment"].get("trackingInfo") or {})
                 .get("number")))
            remaining["qty"] -= quantity
            return {"fulfillmentCreate": {
                "fulfillment": {"id": "gid://shopify/Fulfillment/9",
                                "status": "SUCCESS"},
                "userErrors": []}}

        self.patch_graphql(fake_graphql)

        def ship(pick, qty, tracking):
            pick.carrier_tracking_ref = tracking
            move = pick.move_ids[0]
            move.quantity = qty
            if "picked" in move._fields:
                move.picked = True
            result = pick.button_validate()
            if isinstance(result, dict):  # backorder confirmation wizard
                self.env[result["res_model"]].with_context(
                    **result["context"]).create({}).process()

        ship(picking, 2, "TRACK-A")
        self.run_jobs()
        self.assertEqual(calls["created"], [(2, "TRACK-A")],
                         "first picking -> fulfillment of 2 with tracking")
        backorder = so.picking_ids - picking
        self.assertEqual(len(backorder), 1, "backorder for the remaining 3")
        ship(backorder, 3, "TRACK-B")
        self.run_jobs()
        self.assertEqual(calls["created"], [(2, "TRACK-A"), (3, "TRACK-B")],
                         "two pickings -> two fulfillments")
