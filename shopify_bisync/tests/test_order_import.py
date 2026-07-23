# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""A2/A7: order idempotency, line-resolution ladder, tax policies, discounts,
shipping mapping, financial gating, edits, cancellation, guest checkout."""
from odoo.tests import tagged

from .common import ShopifyBisyncCase, order_payload


@tagged("post_install", "-at_install")
class TestOrderImport(ShopifyBisyncCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.make_product(cls)
        cls.variant = cls.template.product_variant_id
        cls.bind(cls, cls.variant, 770001, inventory_item_id=440001)

    def _so(self, number=1001):
        return self.env["sale.order"].search(
            [("client_order_ref", "=", f"SHOPIFY/{number}"),
             ("company_id", "=", self.company.id)])

    # ------------------------------------------------------------ idempotency
    def test_01_idempotent_import(self):
        payload = order_payload()
        self.OrderSync._import_order(self.instance, payload)
        self.OrderSync._import_order(self.instance, payload)  # redelivery
        self.assertEqual(len(self._so()), 1, "webhook redelivery must no-op")

    # ---------------------------------------------------------------- ladder
    def test_02_ladder_variant_binding(self):
        self.OrderSync._import_order(self.instance, order_payload())
        so = self._so()
        self.assertEqual(so.order_line[0].product_id, self.variant)
        self.assertEqual(so.order_line[0].shopify_bisync_line_id, "880001")

    def test_03_ladder_sku_then_barcode(self):
        by_sku = self.make_product(name="SKU Hit", sku="SKU-HIT").product_variant_id
        by_barcode = self.make_product(
            name="Barcode Hit", sku=False, barcode="123456789").product_variant_id
        payload = order_payload(id=990002, order_number=1002, line_items=[
            {"id": 1, "variant_id": None, "sku": "SKU-HIT", "quantity": 1,
             "price": "10.00", "title": "via sku"},
            {"id": 2, "variant_id": None, "sku": None,
             "barcode": "123456789", "quantity": 1, "price": "10.00",
             "title": "via barcode"},
        ])
        self.OrderSync._import_order(self.instance, payload)
        products = self._so(1002).order_line.mapped("product_id")
        self.assertIn(by_sku, products)
        self.assertIn(by_barcode, products)

    def test_04_ladder_fallback_never_drops(self):
        payload = order_payload(id=990003, order_number=1003, line_items=[
            {"id": 3, "variant_id": 999999, "sku": "GHOST", "quantity": 2,
             "price": "5.00", "title": "Deleted product"}])
        self.OrderSync._import_order(self.instance, payload)
        line = self._so(1003).order_line[0]
        self.assertEqual(line.product_id, self.instance.fallback_product_id,
                         "unmatched line lands on the fallback product")
        self.assertIn("UNMATCHED", line.name)
        mismatch = self.env["shopify.bisync.mismatch"].search(
            [("instance_id", "=", self.instance.id),
             ("kind", "=", "line_unmatched")])
        self.assertTrue(mismatch, "mismatch log entry required")

    # ----------------------------------------------------------------- taxes
    def test_05_shopify_amounts_win_to_the_cent(self):
        self.instance.tax_policy = "shopify"
        self.instance.confirm_policy = "draft"
        payload = order_payload(id=990005, order_number=1005)
        self.OrderSync._import_order(self.instance, payload)
        so = self._so(1005)
        # 19.99*3 - 5.01 + 7.50 shipping + 3.87 taxes = 66.33
        self.assertEqual(f"{so.amount_total:.2f}", "66.33")
        self.assertFalse(so.order_line.tax_ids,
                         "'Shopify wins' lines carry no Odoo tax")

    def test_06_odoo_tax_mode_keeps_note(self):
        self.instance.tax_policy = "odoo"
        payload = order_payload(id=990006, order_number=1006)
        self.OrderSync._import_order(self.instance, payload)
        so = self._so(1006)
        messages = self.env["mail.message"].search(
            [("model", "=", "sale.order"), ("res_id", "=", so.id)])
        self.assertTrue(any("tax lines" in (m.body or "") for m in messages),
                        "Shopify tax lines archived in a note")

    # ------------------------------------------------------------- discounts
    def test_07_per_line_discount_percent(self):
        self.instance.discount_policy = "line"
        payload = order_payload(id=990007, order_number=1007)
        self.OrderSync._import_order(self.instance, payload)
        line = self._so(1007).order_line.filtered(
            lambda l: l.product_id == self.variant)
        # 5.01 / 59.97 = 8.35%
        self.assertEqual(f"{line.discount:.2f}", "8.35")

    def test_08_separate_discount_line(self):
        self.instance.discount_policy = "separate"
        payload = order_payload(id=990008, order_number=1008)
        self.OrderSync._import_order(self.instance, payload)
        discount_lines = self._so(1008).order_line.filtered(
            lambda l: l.product_id == self.instance.discount_product_id)
        self.assertEqual(f"{discount_lines.price_unit:.2f}", "-5.01")

    # -------------------------------------------------------------- shipping
    def test_09_carrier_mapping_and_fallback(self):
        carrier = self.env["delivery.carrier"].create({
            "name": "DHL Test", "delivery_type": "fixed", "fixed_price": 0,
            "product_id": self.env.ref("shopify_bisync.product_shipping").id})
        self.env["shopify.bisync.carrier.map"].create({
            "instance_id": self.instance.id, "shopify_code": "standard",
            "carrier_id": carrier.id})
        payload = order_payload(id=990009, order_number=1009)
        self.OrderSync._import_order(self.instance, payload)
        so = self._so(1009)
        self.assertEqual(so.carrier_id, carrier)
        delivery = so.order_line.filtered("is_delivery")
        self.assertEqual(f"{delivery.price_unit:.2f}", "7.50")

    # ------------------------------------------------------ financial gating
    def test_10_gating_draft_vs_confirm(self):
        self.instance.confirm_policy = "draft"
        self.OrderSync._import_order(
            self.instance, order_payload(id=990010, order_number=1010))
        self.assertEqual(self._so(1010).state, "draft")
        self.instance.confirm_policy = "confirm"
        self.OrderSync._import_order(
            self.instance, order_payload(id=990011, order_number=1011))
        self.assertEqual(self._so(1011).state, "sale")
        # pending payment -> never auto-confirmed
        self.OrderSync._import_order(
            self.instance, order_payload(id=990012, order_number=1012,
                                         financial_status="pending"))
        self.assertEqual(self._so(1012).state, "draft")

    # ------------------------------------------------------------ order edits
    def test_11_edit_undelivered_applies_qty(self):
        self.instance.confirm_policy = "draft"
        self.OrderSync._import_order(
            self.instance, order_payload(id=990013, order_number=1013))
        so = self._so(1013)
        edited = order_payload(id=990013, order_number=1013,
                               _topic="orders/updated")
        edited["line_items"][0]["quantity"] = 5
        self.OrderSync._import_order(self.instance, edited)
        line = so.order_line.filtered(lambda l: l.shopify_bisync_line_id)
        self.assertEqual(line.product_uom_qty, 5)

    def test_12_edit_locked_creates_activity_not_mutation(self):
        """Locked/delivered orders are never silently mutated (same guard:
        ``editable`` is False once the order is locked or lines shipped)."""
        self.instance.confirm_policy = "confirm"
        self.OrderSync._import_order(
            self.instance, order_payload(id=990014, order_number=1014))
        so = self._so(1014)
        so.action_lock()
        line = so.order_line.filtered(lambda l: l.shopify_bisync_line_id)
        edited = order_payload(id=990014, order_number=1014,
                               _topic="orders/updated")
        edited["line_items"][0]["quantity"] = 1
        self.OrderSync._import_order(self.instance, edited)
        self.assertEqual(line.product_uom_qty, 3,
                         "locked line must never be silently mutated")
        self.assertTrue(self.env["shopify.bisync.mismatch"].search_count(
            [("kind", "=", "edit_delivered"),
             ("sale_order_id", "=", so.id)]))
        self.assertTrue(so.activity_ids, "human review activity required")

    # ---------------------------------------------------------- cancellation
    def test_13_cancel_unshipped(self):
        self.instance.confirm_policy = "draft"
        self.OrderSync._import_order(
            self.instance, order_payload(id=990015, order_number=1015))
        cancelled = order_payload(id=990015, order_number=1015,
                                  _topic="orders/cancelled",
                                  cancelled_at="2026-07-21T00:00:00Z",
                                  cancel_reason="customer")
        self.OrderSync._import_order(self.instance, cancelled)
        self.assertEqual(self._so(1015).state, "cancel")

    # -------------------------------------------------------- guest checkout
    def test_14_guest_checkout(self):
        payload = order_payload(id=990016, order_number=1016, customer=None)
        self.OrderSync._import_order(self.instance, payload)
        so = self._so(1016)
        self.assertTrue(so.partner_id)
        self.assertEqual(so.partner_id.name, "Jane Doe")

    # --------------------------------------------------------- multi-currency
    def test_15_currency_pricelist_match(self):
        eur = self.env.ref("base.EUR")
        eur.active = True
        self.env["product.pricelist"].create(
            {"name": "EUR Web", "currency_id": eur.id})
        payload = order_payload(id=990017, order_number=1017, currency="EUR")
        self.OrderSync._import_order(self.instance, payload)
        so = self._so(1017)
        self.assertEqual(so.currency_id, eur,
                         "order currency resolved via a matching pricelist")
        self.assertEqual(so.pricelist_id.currency_id, eur)

    # ----------------------------------------------------------------- risk
    def test_16_risky_order_tagged(self):
        payload = order_payload(id=990018, order_number=1018,
                                risk_recommendation="cancel")
        self.OrderSync._import_order(self.instance, payload)
        so = self._so(1018)
        self.assertIn("Shopify: Risky", so.tag_ids.mapped("name"))
        self.assertTrue(so.activity_ids)

    # ------------------------------------------------------- customer dedup
    def test_17_customer_email_dedup(self):
        self.OrderSync._import_order(
            self.instance, order_payload(id=990019, order_number=1019))
        first_partner = self._so(1019).partner_id
        self.OrderSync._import_order(
            self.instance, order_payload(id=990020, order_number=1020))
        self.assertEqual(self._so(1020).partner_id, first_partner,
                         "same email must reuse the partner")
        self.assertEqual(first_partner.country_id.code, "KE",
                         "country resolved from address code")
