# -*- coding: utf-8 -*-
"""Compositions from BoMs, and production reported as processing movements."""

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from odoo.addons.x_ke_etims.tests.common import KeEtimsCase


@tagged("post_install", "-at_install")
class TestMrp(KeEtimsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.sudo().group_ids = [Command.link(cls.env.ref("mrp.group_mrp_manager").id)]
        cls.flour = cls.make_product("Wheat flour 1kg", 150.0, cls.st16)
        cls.bread = cls.make_product("White bread 400g", 70.0, cls.ke_tax("STEX"))
        (cls.flour | cls.bread).write({"is_storable": True})
        cls.bom = cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.bread.product_tmpl_id.id,
            "product_qty": 10.0,
            "company_id": cls.ke_company.id,
            "bom_line_ids": [Command.create({"product_id": cls.flour.id, "product_qty": 4.0})],
        })
        cls.stock = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.ke_company.id)], limit=1).lot_stock_id

    def test_a_bom_is_sent_as_the_composition_of_its_product(self):
        self.assertTrue(self.bom.ke_etims_can_send, "The demo backend takes compositions.")
        self.bom.action_ke_etims_send_composition()
        self.bom.invalidate_recordset(["ke_etims_state"])
        self.assertEqual(self.bom.ke_etims_state, "sent")
        document = self.env["ke.etims.document"].search(
            [("res_model", "=", "mrp.bom"), ("res_id", "=", self.bom.id)])
        composition = self.bom._ke_etims_prepare(document)
        self.assertEqual(len(composition), 1)
        self.assertEqual(composition[0].item_code, self.bread.product_tmpl_id.ke_etims_item_code)
        self.assertEqual(composition[0].component_item_code,
                         self.flour.product_tmpl_id.ke_etims_item_code)
        self.assertEqual(composition[0].component_quantity, 0.4, "Per one loaf.")
        # Sent again after the BoM changes, as the same document.
        self.bom.bom_line_ids.product_qty = 5.0
        self.bom.action_ke_etims_send_composition()
        self.assertEqual(self.env["ke.etims.document"].search_count(
            [("res_model", "=", "mrp.bom"), ("res_id", "=", self.bom.id)]), 1)

    def test_digitax_has_no_compositions_and_the_button_hides(self):
        self.device.active = False
        self.make_device(self.ke_company, backend="digitax", environment="test",
                         api_key="key").state = "initialised"
        self.bom.invalidate_recordset()
        self.assertFalse(self.bom.ke_etims_can_send)
        with self.assertRaises(UserError):
            self.bom.action_ke_etims_send_composition()

    def test_production_is_reported_as_processing(self):
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": self.flour.id, "location_id": self.stock.id,
            "inventory_quantity": 8.0}).action_apply_inventory()
        form = Form(self.env["mrp.production"].with_company(self.ke_company))
        form.product_id = self.bread
        form.bom_id = self.bom
        form.product_qty = 10.0
        production = form.save()
        production.action_confirm()
        production.qty_producing = 10.0
        production.move_raw_ids.write({"quantity": 4.0, "picked": True})
        production.button_mark_done()
        self.assertEqual(production.state, "done")
        production_moves = production.move_raw_ids | production.move_finished_ids
        documents = self.env["ke.etims.document"].search(
            [("kind", "=", "stock_io"), ("res_model", "=", "stock.move"),
             ("res_id", "in", production_moves.ids)])
        types = {}
        for document in documents:
            move = document._ke_etims_source()
            types[move.product_id] = move._ke_etims_io_type()
        self.assertEqual(types[self.flour], "14", "Consumed in processing.")
        self.assertEqual(types[self.bread], "05", "Made by processing.")
        self.run_queue()
        self.assertEqual(set(documents.mapped("state")), {"sent"})
