# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Bi-directional order lifecycle: fulfillment-status import, mark-as-paid
push, cancellation push, refund push - each loop-guarded so an inbound event
never bounces straight back to Shopify."""
from odoo.tests import tagged

from .common import ShopifyBisyncCase, order_payload


@tagged("post_install", "-at_install")
class TestBidirectional(ShopifyBisyncCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.make_product(cls, name="Bi Widget", sku="BI-1")
        cls.variant = cls.template.product_variant_id
        cls.bind(cls, cls.variant, 770001, inventory_item_id=440001)

    def _so(self, number):
        return self.env["sale.order"].search(
            [("client_order_ref", "=", f"SHOPIFY/{number}")])

    def _seed_stock(self, qty=10):
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": self.variant.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "inventory_quantity": qty}).action_apply_inventory()

    # ------------------------------------------------ fulfillment IMPORT ----
    def test_01_fulfillment_import_validates_delivery(self):
        self.instance.confirm_policy = "confirm"
        self._seed_stock()
        self.OrderSync._import_order(
            self.instance, order_payload(id=990101, order_number=1101))
        so = self._so(1101)
        self.assertEqual(so.state, "sale")
        fulfillment = {"id": 5501, "order_id": 990101, "status": "success",
                       "line_items": [{"id": 880001, "quantity": 3}]}
        self.env["shopify.bisync.fulfillment.sync"]._import_fulfillment(
            self.instance, fulfillment)
        self.assertTrue(all(p.state == "done" for p in so.picking_ids),
                        "delivery validated to reflect Shopify fulfillment")
        self.assertTrue(so._shopify_fulfillment_done(5501))
        # Loop guard: reflecting a Shopify fulfillment must NOT queue an export.
        self.assertFalse(self.Job.search_count([
            ("kind", "=", "fulfillment"), ("direction", "=", "out")]))
        # Idempotent redelivery.
        self.env["shopify.bisync.fulfillment.sync"]._import_fulfillment(
            self.instance, fulfillment)
        self.assertEqual(len(so.picking_ids.filtered(
            lambda p: p.state == "done")), 1)

    def test_02_fulfillment_import_disabled(self):
        self.instance.write({"confirm_policy": "confirm",
                             "import_fulfillment_status": False})
        self._seed_stock()
        self.OrderSync._import_order(
            self.instance, order_payload(id=990102, order_number=1102))
        so = self._so(1102)
        self.env["shopify.bisync.fulfillment.sync"]._import_fulfillment(
            self.instance, {"id": 5502, "order_id": 990102, "status": "success",
                            "line_items": [{"id": 880001, "quantity": 3}]})
        self.assertFalse(any(p.state == "done" for p in so.picking_ids))

    # ---------------------------------------------------- mark as paid ------
    def test_03_manual_mark_paid_enqueues(self):
        self.instance.confirm_policy = "confirm"
        self._seed_stock()
        self.OrderSync._import_order(
            self.instance, order_payload(id=990103, order_number=1103))
        so = self._so(1103)
        so.action_shopify_mark_paid()
        self.assertTrue(self.Job.search_count([
            ("kind", "=", "paid"), ("direction", "=", "out"),
            ("instance_id", "=", self.instance.id)]))

    def test_04_mark_paid_job_calls_mutation(self):
        self.instance.push_paid_status = True
        job = self.Job.enqueue(self.instance, "out", "paid",
                               {"order_id": 990104, "sale_order_id": 0},
                               lock_key="order:990104")
        calls = []

        def fake(instance, query, variables=None):
            calls.append(query)
            return {"orderMarkAsPaid": {"order": {"id": "x"},
                                        "userErrors": []}}
        self.patch_graphql(fake)
        job._run_one()
        self.assertEqual(job.state, "done")
        self.assertIn("orderMarkAsPaid", calls[0])

    # ------------------------------------------------------ cancel push -----
    def test_05_cancel_push_on_odoo_cancel(self):
        self.instance.push_cancellations = True
        self.OrderSync._import_order(
            self.instance, order_payload(id=990105, order_number=1105))
        so = self._so(1105)
        so._action_cancel()
        self.assertEqual(so.state, "cancel")
        self.assertTrue(self.Job.search_count([
            ("kind", "=", "cancel"), ("direction", "=", "out")]),
            "cancelling in Odoo pushes a cancellation job")

    def test_06_imported_cancel_does_not_push(self):
        self.instance.push_cancellations = True
        self.OrderSync._import_order(
            self.instance, order_payload(id=990106, order_number=1106))
        cancelled = order_payload(id=990106, order_number=1106,
                                  _topic="orders/cancelled",
                                  cancel_reason="customer")
        self.OrderSync._import_order(self.instance, cancelled)
        self.assertEqual(self._so(1106).state, "cancel")
        self.assertFalse(self.Job.search_count([("kind", "=", "cancel")]),
                         "importing a cancel must not bounce it back")

    # ---------------------------------------------------------- refunds -----
    def test_07_posted_credit_note_pushes_refund(self):
        journal = self.env["account.journal"].search(
            [("type", "=", "sale"), ("company_id", "=", self.company.id)],
            limit=1)
        if not journal:
            self.skipTest("No accounting chart in this DB")
        self.instance.write({"confirm_policy": "confirm",
                             "refund_export_policy": "record"})
        self._seed_stock()
        self.OrderSync._import_order(
            self.instance, order_payload(id=990107, order_number=1107))
        so = self._so(1107)
        invoice = so._create_invoices()
        invoice.action_post()
        reversal = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=invoice.ids).create({
                "journal_id": invoice.journal_id.id})
        action = reversal.refund_moves()
        credit = self.env["account.move"].browse(action["res_id"])
        self.assertFalse(credit.shopify_bisync_refund_id,
                         "an Odoo-created credit note is not Shopify-originated")
        credit.action_post()
        self.assertTrue(self.Job.search_count([
            ("kind", "=", "refund_out"), ("direction", "=", "out")]),
            "posting a credit note for a Shopify order queues a refund push")

    def test_08_imported_refund_does_not_push_back(self):
        self.instance.refund_export_policy = "record"
        move = self.env["account.move"].create({
            "move_type": "out_refund",
            "shopify_bisync_refund_id": "8899",  # marks Shopify-originated
        })
        move._shopify_enqueue_refund_export()
        self.assertFalse(self.Job.search_count([("kind", "=", "refund_out")]),
                         "a Shopify-originated refund must never push back")
