# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""A1/A7: product export payloads (GraphQL productSet), checksum no-op with
ZERO API calls, multi-variant import creating attributes + variant bindings.
"""
from odoo.tests import tagged

from .common import ShopifyBisyncCase, gql_product_set_response


@tagged("post_install", "-at_install")
class TestProductSync(ShopifyBisyncCase):

    def test_01_export_creates_binding_and_variants(self):
        template = self.make_product(name="Export Me", sku="EXP-1")
        mock = self.patch_graphql(lambda *a, **k: gql_product_set_response(
            variant_specs=[{"id": 770301, "sku": "EXP-1",
                            "inventory_item_id": 440301, "options": []}]))
        self.ProductSync._export_product(self.instance,
                                         {"res_id": template.id})
        self.assertEqual(mock.call_count, 1)
        query = mock.call_args_list[0].args[1]
        variables = mock.call_args_list[0].args[2]
        self.assertIn("productSet", query)
        self.assertEqual(variables["input"]["title"], "Export Me")
        self.assertEqual(
            variables["input"]["variants"][0]["optionValues"],
            [{"optionName": "Title", "name": "Default Title"}])
        binding = self.Binding.get(self.env, self.instance,
                                   "product.template", record=template)
        self.assertEqual(binding.external_id, "660001")
        vbinding = self.Binding.get(
            self.env, self.instance, "product.product",
            record=template.product_variant_id)
        self.assertEqual(vbinding.external_id, "770301")
        self.assertEqual(vbinding.inventory_item_id, "440301")

    def test_02_checksum_noop_makes_zero_api_calls(self):
        template = self.make_product(name="Stable", sku="STB-1")
        mock = self.patch_graphql(lambda *a, **k: gql_product_set_response(
            variant_specs=[{"id": 770302, "sku": "STB-1",
                            "inventory_item_id": 440302, "options": []}]))
        self.ProductSync._export_product(self.instance, {"res_id": template.id})
        self.assertEqual(mock.call_count, 1)
        # Unchanged record -> checksum identical -> not a single API call.
        self.ProductSync._export_product(self.instance, {"res_id": template.id})
        self.assertEqual(mock.call_count, 1,
                         "no-op export must skip the API entirely")
        # A real change busts the checksum.
        template.with_context(shopify_bisync_skip_trigger=True).write(
            {"name": "Changed"})
        self.ProductSync._export_product(self.instance, {"res_id": template.id})
        self.assertEqual(mock.call_count, 2)

    def test_03_archive_exports_archived_status(self):
        template = self.make_product(name="Bye", sku="BYE-1")
        captured = {}

        def capture(instance, query, variables=None):
            captured.update(variables or {})
            return gql_product_set_response(
                variant_specs=[{"id": 770303, "sku": "BYE-1",
                                "inventory_item_id": 440303, "options": []}])
        self.patch_graphql(capture)
        self.ProductSync._export_product(self.instance, {"res_id": template.id})
        template.with_context(shopify_bisync_skip_trigger=True).write(
            {"active": False})
        self.ProductSync._export_product(self.instance, {"res_id": template.id})
        self.assertEqual(captured["input"]["status"], "ARCHIVED")

    def test_04_import_multi_variant(self):
        payload = {
            "id": 660400, "title": "Tee", "body_html": "",
            "status": "active", "updated_at": "2026-07-21T10:00:00Z",
            "options": [
                {"name": "Size", "position": 1, "values": ["S", "M"]},
                {"name": "Color", "position": 2, "values": ["Red", "Blue"]},
            ],
            "variants": [
                {"id": 771000 + i, "sku": f"TEE-{s}-{c}",
                 "inventory_item_id": 441000 + i, "price": "15.00",
                 "option1": s, "option2": c, "grams": 200}
                for i, (s, c) in enumerate(
                    [(s, c) for s in ("S", "M") for c in ("Red", "Blue")])
            ],
            "images": [],
        }
        self.ProductSync._import_product(self.instance, payload)
        binding = self.Binding.get(self.env, self.instance,
                                   "product.template", external_id=660400)
        template = binding.resolve()
        self.assertEqual(template.name, "Tee")
        self.assertEqual(template.product_variant_count, 4)
        self.assertEqual(
            sorted(template.attribute_line_ids.mapped("attribute_id.name")),
            ["Color", "Size"])
        variant_bindings = self.Binding.search([
            ("instance_id", "=", self.instance.id),
            ("res_model", "=", "product.product")])
        self.assertEqual(len(variant_bindings), 4)
        self.assertTrue(all(variant_bindings.mapped("inventory_item_id")))
        skus = sorted(template.product_variant_ids.mapped("default_code"))
        self.assertEqual(skus, ["TEE-M-Blue", "TEE-M-Red",
                                "TEE-S-Blue", "TEE-S-Red"])

    def test_05_import_then_export_is_noop(self):
        """Round-trip: importing writes the export checksum, so the echo
        export produces zero API calls."""
        payload = {
            "id": 660500, "title": "Round Trip", "body_html": "",
            "status": "active", "updated_at": "2026-07-21T10:00:00Z",
            "options": [{"name": "Title", "position": 1,
                         "values": ["Default Title"]}],
            "variants": [{"id": 771500, "sku": "RT-1", "price": "9.99",
                          "inventory_item_id": 441500,
                          "option1": "Default Title", "grams": 100}],
            "images": [],
        }
        self.ProductSync._import_product(self.instance, payload)
        binding = self.Binding.get(self.env, self.instance,
                                   "product.template", external_id=660500)
        mock = self.patch_graphql(lambda *a, **k: gql_product_set_response())
        self.ProductSync._export_product(self.instance,
                                         {"res_id": binding.res_id})
        self.assertEqual(mock.call_count, 0,
                         "freshly imported product must not bounce back")

    def test_06_write_trigger_enqueues_once(self):
        template = self.make_product(name="Trigger", sku="TRG-1")
        self.bind(template, 660600)
        template.write({"name": "Trigger Changed"})
        jobs = self.Job.search([
            ("instance_id", "=", self.instance.id), ("kind", "=", "product"),
            ("direction", "=", "out"), ("state", "=", "pending")])
        self.assertEqual(len(jobs), 1)
        template.write({"name": "Trigger Changed Again"})
        jobs = self.Job.search([
            ("instance_id", "=", self.instance.id), ("kind", "=", "product"),
            ("direction", "=", "out"), ("state", "=", "pending")])
        self.assertEqual(len(jobs), 1, "identical pending export debounced")

    def test_07_price_export_uses_bulk_update(self):
        template = self.make_product(name="Priced", sku="PRC-1", price=100.0)
        self.bind(template, 660700)
        self.bind(template.product_variant_id, 770700,
                  inventory_item_id=440700)
        pricelist = self.env["product.pricelist"].create({
            "name": "Web -10%",
            "item_ids": [(0, 0, {
                "applied_on": "3_global",
                "compute_price": "percentage", "percent_price": 10})],
        })
        self.instance.write({"pricelist_id": pricelist.id,
                             "compare_at_policy": "list_price"})
        captured = {}

        def capture(instance, query, variables=None):
            captured["query"] = query
            captured.update(variables or {})
            return {"productVariantsBulkUpdate": {"userErrors": []}}
        self.patch_graphql(capture)
        self.ProductSync._export_prices(self.instance, {"res_id": template.id})
        self.assertIn("productVariantsBulkUpdate", captured["query"])
        entry = captured["variants"][0]
        self.assertEqual(entry["price"], "90.00")
        self.assertEqual(entry["compareAtPrice"], "100.00")
