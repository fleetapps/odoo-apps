# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Gallery sync, the dry-run diff, the conflict ledger and payout fees."""
import base64

from unittest.mock import patch

from odoo.tests import tagged

from .common import ShopifyBisyncCase

# 1x1 PNGs that differ, so their sha1s differ.
PNG_A = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")
PNG_B = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9"
    "awAAAABJRU5ErkJggg==")


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


@tagged("post_install", "-at_install")
class TestImagesAndPreview(ShopifyBisyncCase):

    def _payload(self, srcs):
        return {
            "id": 661001, "title": "Gallery Widget", "status": "active",
            "updated_at": "2026-07-25T10:00:00Z", "options": [],
            "images": [{"src": src} for src in srcs],
            "variants": [{"id": 771001, "sku": "GAL-1", "price": "10.00"}],
        }

    def _patch_download(self, blobs):
        mapping = dict(blobs)

        def fake_get(url, timeout=None):
            return _Resp(mapping[url])
        patcher = patch("odoo.addons.shopify_bisync.models.product_sync."
                        "requests.get", side_effect=fake_get)
        self.addCleanup(patcher.stop)
        return patcher.start()

    # ------------------------------------------------------------- images ---
    def test_01_import_sets_main_image_and_records_source_hash(self):
        self._patch_download([("https://cdn/a.png?v=1", PNG_A),
                              ("https://cdn/b.png?v=1", PNG_B)])
        self.ProductSync._import_product(
            self.instance, self._payload(["https://cdn/a.png?v=1",
                                          "https://cdn/b.png?v=1"]))
        binding = self.Binding.search([("res_model", "=", "product.template"),
                                       ("external_id", "=", "661001")])
        tmpl = binding.resolve()
        # Byte equality is not asserted: Odoo's Image field may re-encode on
        # write, so the meaningful guarantees are "something arrived" and
        # "the source list was fingerprinted".
        self.assertTrue(tmpl.image_1920, "main image imported")
        self.assertTrue(binding.image_source_hash)

    def test_02_unchanged_urls_skip_the_download(self):
        mock = self._patch_download([("https://cdn/a.png?v=1", PNG_A)])
        payload = self._payload(["https://cdn/a.png?v=1"])
        self.ProductSync._import_product(self.instance, payload)
        first_calls = mock.call_count
        binding = self.Binding.search([("res_model", "=", "product.template"),
                                       ("external_id", "=", "661001")])
        # Re-run the image step alone: same URLs must not re-download.
        self.ProductSync._import_images(
            self.instance, payload, binding.resolve(), binding)
        self.assertEqual(mock.call_count, first_calls,
                         "unchanged image URLs must cost no download")

    def test_03_gallery_beyond_main_image_is_handled_or_logged(self):
        self._patch_download([("https://cdn/a.png?v=1", PNG_A),
                              ("https://cdn/b.png?v=1", PNG_B)])
        self.ProductSync._import_product(
            self.instance, self._payload(["https://cdn/a.png?v=1",
                                          "https://cdn/b.png?v=1"]))
        binding = self.Binding.search([("res_model", "=", "product.template"),
                                       ("external_id", "=", "661001")])
        tmpl = binding.resolve()
        if "product_template_image_ids" in tmpl._fields:
            self.assertEqual(len(tmpl.product_template_image_ids), 1,
                             "second Shopify image becomes a gallery row")
        else:
            # website_sale absent: the extra image must be reported, not
            # silently dropped.
            self.assertTrue(self.env["shopify.bisync.mismatch"].search_count(
                [("kind", "=", "image_gallery")]))

    def test_04_image_policy_off_imports_nothing(self):
        self.instance.image_policy = "off"
        mock = self._patch_download([("https://cdn/a.png?v=1", PNG_A)])
        self.ProductSync._import_product(
            self.instance, self._payload(["https://cdn/a.png?v=1"]))
        self.assertEqual(mock.call_count, 0)

    def test_05_odoo_images_dedupes_and_orders(self):
        tmpl = self.make_product(name="Imaged", sku="IMG-1")
        tmpl.with_context(shopify_bisync_skip_trigger=True).write(
            {"image_1920": base64.b64encode(PNG_A)})
        images = self.ProductSync._odoo_images(tmpl)
        self.assertEqual(len(images), 1)
        self.assertEqual(len(images[0][0]), 40, "entries are keyed by sha1")

    # ------------------------------------------------------------ dry run ---
    def test_06_preview_reports_creation_for_unbound_product(self):
        tmpl = self.make_product(name="Never Synced", sku="NEW-1")
        rows = self.ProductSync.diff_against_shopify(self.instance, tmpl)
        self.assertEqual(len(rows), 1)
        self.assertIn("created", rows[0]["shopify"])

    def test_07_preview_diffs_title_without_writing(self):
        tmpl = self.make_product(name="Odoo Title", sku="PRV-1")
        self.bind(tmpl, 661101)
        calls = []

        def fake(instance, query, variables=None):
            calls.append(query)
            return {"product": {
                "id": "gid://shopify/Product/661101",
                "legacyResourceId": "661101", "title": "Shopify Title",
                "descriptionHtml": "", "status": "ACTIVE", "tags": [],
                "productType": "", "options": [],
                "media": {"nodes": []}, "variants": {"nodes": []}}}
        self.patch_graphql(fake)
        rows = self.ProductSync.diff_against_shopify(self.instance, tmpl)
        titles = [r for r in rows if r["odoo"] == "Odoo Title"]
        self.assertTrue(titles, "title difference must be reported")
        self.assertEqual(titles[0]["shopify"], "Shopify Title")
        self.assertTrue(all("mutation" not in c for c in calls),
                        "a preview must never mutate Shopify")

    def test_08_preview_wizard_opens_and_pushes(self):
        tmpl = self.make_product(name="Pushable", sku="PSH-1")
        self.instance.sync_products = "both"
        action = self.env["shopify.bisync.preview"].open_for(tmpl)
        wizard = self.env["shopify.bisync.preview"].browse(action["res_id"])
        self.assertTrue(wizard.has_changes)
        wizard.action_push()
        self.assertTrue(self.Job.search_count([
            ("kind", "=", "product"), ("direction", "=", "out")]))

    # ----------------------------------------------------- conflict ledger --
    def test_09_conflict_is_recorded_as_a_row(self):
        payload = {"id": 661201, "title": "Shopify Name", "status": "active",
                   "updated_at": "2026-07-26T10:00:00Z", "options": [],
                   "images": [],
                   "variants": [{"id": 771201, "sku": "CFL-1",
                                 "price": "10.00"}]}
        self.ProductSync._import_product(self.instance, payload)
        binding = self.Binding.search([("res_model", "=", "product.template"),
                                       ("external_id", "=", "661201")])
        tmpl = binding.resolve()
        # Edit in Odoo AND on Shopify since the last sync.
        tmpl.with_context(shopify_bisync_skip_trigger=True).write(
            {"name": "Odoo Name"})
        binding.odoo_write_date = "2026-07-26 09:00:00"
        payload["title"] = "Shopify Newer"
        payload["updated_at"] = "2026-07-27T10:00:00Z"
        self.instance.conflict_policy = "shopify_wins"
        self.ProductSync._import_product(self.instance, payload)
        conflict = self.env["shopify.bisync.conflict"].search(
            [("res_id", "=", tmpl.id)], limit=1)
        self.assertTrue(conflict, "the resolution must be queryable, not just chatter")
        self.assertEqual(conflict.winner, "shopify")
        self.assertEqual(conflict.policy, "shopify_wins")
        self.assertIn("name", conflict.field_names)
        self.assertNotIn("categ_id", conflict.field_names,
                         "untouched relational fields must not be listed")

    # -------------------------------------------------------- payout fees ---
    def test_10_fee_entry_balances_the_clearing_account(self):
        bank = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)],
            limit=1)
        misc = self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", self.company.id)],
            limit=1)
        expense = self.env["account.account"].search(
            [("company_ids", "in", self.company.id),
             ("account_type", "=", "expense")], limit=1)
        if not (bank and misc and expense and bank.default_account_id):
            self.skipTest("No accounting chart in this DB")
        self.instance.write({"payout_journal_id": bank.id,
                             "payout_fee_journal_id": misc.id,
                             "payout_fee_account_id": expense.id})
        payout = self.env["shopify.bisync.payout"].create({
            "instance_id": self.instance.id,
            "shopify_payout_id": "700900",
            "currency_id": self.company.currency_id.id,
            "amount": 63.0, "charge_total": 66.33, "fee_total": 3.33,
        })
        payout.action_book_fees()
        move = payout.fee_move_id
        self.assertTrue(move, "fees must produce a posted entry")
        self.assertEqual(move.state, "posted")
        self.assertAlmostEqual(sum(move.line_ids.mapped("balance")), 0.0, 2)
        fee_line = move.line_ids.filtered(lambda l: l.account_id == expense)
        self.assertAlmostEqual(fee_line.balance, 3.33, 2,
                               "the fee is an expense, i.e. a debit")
        payout.action_book_fees()
        self.assertEqual(payout.fee_move_id, move, "booking twice is a no-op")

    def test_11_unconfigured_fees_do_not_block_reconciliation(self):
        payout = self.env["shopify.bisync.payout"].create({
            "instance_id": self.instance.id,
            "shopify_payout_id": "700901",
            "currency_id": self.company.currency_id.id,
            "amount": 63.0, "fee_total": 3.33,
        })
        journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)],
            limit=1)
        if not journal:
            self.skipTest("No accounting chart in this DB")
        self.instance.payout_journal_id = journal.id
        payout.action_register_payments()
        self.assertEqual(payout.state, "reconciled")
        self.assertFalse(payout.fee_move_id)

    def test_12_difference_exposes_a_payout_that_does_not_close(self):
        payout = self.env["shopify.bisync.payout"].create({
            "instance_id": self.instance.id,
            "shopify_payout_id": "700902",
            "currency_id": self.company.currency_id.id,
            "amount": 100.0,
        })
        self.env["shopify.bisync.payout.transaction"].create({
            "payout_id": payout.id, "shopify_transaction_id": "1",
            "transaction_type": "charge", "amount": 90.0, "net": 90.0,
        })
        self.assertAlmostEqual(payout.difference, 10.0, 2)
