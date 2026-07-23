# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""A3/A7: the conflict engine - all three policies + the audit message.

Scenario (mirrors the acceptance test): a product changed on BOTH sides
between syncs. The inbound webhook carries a newer Shopify revision while
Odoo's write_date moved past the binding fingerprint."""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import ShopifyBisyncCase


def product_webhook(title, updated_at):
    return {
        "id": 660100, "title": title, "body_html": "<p>desc</p>",
        "status": "active", "updated_at": updated_at,
        "options": [{"name": "Title", "position": 1,
                     "values": ["Default Title"]}],
        "variants": [{"id": 770100, "sku": "CONF-1", "price": "10.00",
                      "inventory_item_id": 440100, "option1": "Default Title",
                      "grams": 500}],
        "images": [],
    }


@tagged("post_install", "-at_install")
class TestConflictEngine(ShopifyBisyncCase):

    def _seed(self):
        """Bound product whose fingerprints say 'in sync at T0', then an Odoo
        edit AFTER T0 (both-sides-changed precondition)."""
        template = self.make_product(name="Original", sku="CONF-1")
        binding = self.bind(template, 660100)
        self.bind(template.product_variant_id, 770100,
                  inventory_item_id=440100)
        binding.write({
            "odoo_write_date": template.write_date - timedelta(hours=2),
            "external_updated_at": fields.Datetime.now() - timedelta(hours=2),
        })
        template.with_context(shopify_bisync_skip_trigger=True).write(
            {"name": "Odoo Edit"})
        return template, binding

    def _inbound(self, template, updated_at=None):
        updated_at = updated_at or (
            fields.Datetime.now() + timedelta(minutes=5)
        ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        self.ProductSync._import_product(
            self.instance, product_webhook("Shopify Edit", updated_at))

    def test_01_shopify_wins(self):
        self.instance.conflict_policy = "shopify_wins"
        template, __ = self._seed()
        self._inbound(template)
        self.assertEqual(template.name, "Shopify Edit")
        message = self.last_message(template)
        self.assertIn("conflict", (message.body or "").lower())
        self.assertIn("Shopify", message.body)

    def test_02_odoo_wins_skips_and_reexports(self):
        self.instance.conflict_policy = "odoo_wins"
        template, __ = self._seed()
        self._inbound(template)
        self.assertEqual(template.name, "Odoo Edit",
                         "import must be skipped under odoo_wins")
        export_jobs = self.Job.search([
            ("instance_id", "=", self.instance.id),
            ("direction", "=", "out"), ("kind", "=", "product"),
            ("state", "=", "pending")])
        self.assertTrue(export_jobs, "odoo_wins must enqueue the re-export")
        self.assertIn("conflict",
                      (self.last_message(template).body or "").lower())

    def test_03_newest_wins_shopify_newer(self):
        self.instance.conflict_policy = "newest_wins"
        template, __ = self._seed()
        self._inbound(template)  # inbound timestamp is in the future
        self.assertEqual(template.name, "Shopify Edit")

    def test_04_newest_wins_odoo_newer(self):
        self.instance.conflict_policy = "newest_wins"
        template, binding = self._seed()
        # Shopify revision newer than the fingerprint but older than the
        # Odoo edit -> Odoo wins.
        stale = (binding.external_updated_at + timedelta(minutes=10)
                 ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        self._inbound(template, updated_at=stale)
        self.assertEqual(template.name, "Odoo Edit")

    def test_05_no_conflict_when_odoo_unchanged(self):
        """Plain inbound update (Odoo untouched since last sync) applies
        without any conflict chatter."""
        template = self.make_product(name="Quiet", sku="CONF-1")
        binding = self.bind(template, 660100)
        binding.write({
            "odoo_write_date": template.write_date,
            "external_updated_at": fields.Datetime.now() - timedelta(hours=1),
        })
        messages_before = self.env["mail.message"].search_count(
            [("model", "=", "product.template"),
             ("res_id", "=", template.id)])
        self._inbound(template)
        self.assertEqual(template.name, "Shopify Edit")
        conflict_messages = self.env["mail.message"].search(
            [("model", "=", "product.template"),
             ("res_id", "=", template.id)]).filtered(
            lambda m: "conflict" in (m.body or "").lower())
        self.assertFalse(conflict_messages)
        self.assertGreaterEqual(
            self.env["mail.message"].search_count(
                [("model", "=", "product.template"),
                 ("res_id", "=", template.id)]), messages_before)

    def test_06_echo_suppression(self):
        """Inbound revision <= fingerprint (our own export's webhook echo)
        must be a hard no-op."""
        template, binding = self._seed()
        echo = (binding.external_updated_at - timedelta(minutes=1)
                ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        self._inbound(template, updated_at=echo)
        self.assertEqual(template.name, "Odoo Edit", "echo must not import")
