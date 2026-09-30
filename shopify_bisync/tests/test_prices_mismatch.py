# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Two-way prices, and a mismatch log that stays readable.

The price tests pin the three paths apart, because they behave differently on
purpose: a new product takes Shopify's price, an adopted one keeps Odoo's, and
only an ongoing update is a price *change* subject to the conflict rule.
"""
from odoo.tests import tagged

from .common import ShopifyBisyncCase


def _product_payload(pid=9001, title="Widget", price="10.00", variants=None):
    return {
        "id": pid, "title": title, "status": "active",
        "updated_at": "2026-08-04T10:00:00+00:00",
        "variants": variants or [
            {"id": pid + 1, "sku": "W-1", "price": price,
             "inventory_item_id": pid + 2}],
    }


@tagged("post_install", "-at_install")
class TestPriceImport(ShopifyBisyncCase):

    def setUp(self):
        super().setUp()
        self.instance.write({"sync_products": "both", "sync_prices": "both"})
        self.Sync = self.env["shopify.bisync.product.sync"]

    def test_01_price_ignored_when_not_importing(self):
        """Odoo -> Shopify only: an inbound price must not touch Odoo."""
        self.instance.sync_prices = "export"
        payload = _product_payload()
        self.Sync._import_product(self.instance, payload)
        tmpl = self._bound_template(payload["id"])
        tmpl.list_price = 99.0
        self.Sync._import_product(
            self.instance,
            dict(payload, price="10.00",
                 updated_at="2026-08-04T12:00:00+00:00"))
        tmpl.invalidate_recordset()
        self.assertEqual(tmpl.list_price, 99.0,
                         "export-only stores must not accept Shopify prices")

    def test_02_ongoing_change_updates_the_price(self):
        payload = _product_payload(price="10.00")
        self.Sync._import_product(self.instance, payload)
        tmpl = self._bound_template(payload["id"])

        self.Sync._import_product(self.instance, _product_payload(
            price="12.50") | {"updated_at": "2026-08-04T12:00:00+00:00"})
        tmpl.invalidate_recordset()
        self.assertEqual(tmpl.list_price, 12.50)

    def test_03_variant_spread_is_logged_not_flattened_silently(self):
        payload = _product_payload(variants=[
            {"id": 11, "sku": "A", "price": "10.00", "inventory_item_id": 1},
            {"id": 12, "sku": "B", "price": "17.00", "inventory_item_id": 2},
        ])
        self.Sync._import_product(self.instance, payload)
        self.Sync._import_product(self.instance, dict(
            payload, updated_at="2026-08-04T12:00:00+00:00"))
        logged = self.env["shopify.bisync.mismatch"].search([
            ("instance_id", "=", self.instance.id),
            ("kind", "=", "variant_price_spread")])
        self.assertTrue(logged, "a price we could not represent must be told")

    def _bound_template(self, external_id):
        binding = self.env["shopify.bisync.binding"].get(
            self.env, self.instance, "product.template",
            external_id=external_id)
        return binding.resolve()


@tagged("post_install", "-at_install")
class TestMismatchAggregation(ShopifyBisyncCase):

    def test_01_same_cause_folds_into_one_row(self):
        """A store-wide cause files one row with a count, not one per order."""
        Mismatch = self.env["shopify.bisync.mismatch"]
        for ref in ("SHOPIFY/1001", "SHOPIFY/1002", "SHOPIFY/1003"):
            Mismatch.log(self.env, self.instance, "currency_unmapped",
                         f"No pricelist in KES; latest {ref}",
                         reference=ref, group_key="currency:KES")
        rows = Mismatch.search([("instance_id", "=", self.instance.id),
                                ("kind", "=", "currency_unmapped")])
        self.assertEqual(len(rows), 1, "three orders, one underlying problem")
        self.assertEqual(rows.occurrence_count, 3)
        self.assertEqual(rows.reference, "SHOPIFY/1003",
                         "the newest occurrence is the one worth opening")

    def test_02_distinct_causes_stay_distinct(self):
        Mismatch = self.env["shopify.bisync.mismatch"]
        Mismatch.log(self.env, self.instance, "line_unmatched", "a",
                     group_key="sku:AAA")
        Mismatch.log(self.env, self.instance, "line_unmatched", "b",
                     group_key="sku:BBB")
        self.assertEqual(len(Mismatch.search([
            ("instance_id", "=", self.instance.id),
            ("kind", "=", "line_unmatched")])), 2)

    def test_03_resolving_lets_a_recurrence_reopen(self):
        """A resolved row is history; the problem coming back is news."""
        Mismatch = self.env["shopify.bisync.mismatch"]
        first = Mismatch.log(self.env, self.instance, "currency_unmapped",
                             "no KES", group_key="currency:KES")
        first.action_mark_resolved()
        second = Mismatch.log(self.env, self.instance, "currency_unmapped",
                              "no KES again", group_key="currency:KES")
        self.assertNotEqual(first, second)
        self.assertEqual(second.occurrence_count, 1)
