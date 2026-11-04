from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockBook(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids += cls.env.ref("odin_stock_book.group_stock_book_supervisor")
        cls.wh = cls.env["stock.warehouse"].search([("company_id", "=", cls.env.company.id)], limit=1)
        cls.stock = cls.wh.lot_stock_id
        unit = cls.env.ref("uom.product_uom_unit")
        litre = cls.env.ref("uom.product_uom_litre")
        categ = cls.env["product.category"].create({"name": "Finished Goods / Handwash", "property_cost_method": "fifo"})
        P = cls.env["product.product"]
        def make(name, code, uom, cost=1.0):
            return P.create({"name": name, "default_code": code, "is_storable": True, "type": "consu",
                             "uom_id": uom.id, "categ_id": categ.id, "standard_price": cost})
        cls.fg = make("Aqua Blue Handwash 500 Ml", "FIN-PAK-002", unit, 75)
        cls.bulk = make("Aqua Blue Handwash (Bulk)", "FIN-BULK-001", litre, 53)
        cls.bottle = make("500 Ml Handwash Round", "PAK-BOT-014", unit, 15)
        cls.carton = make("Type G - Hand Wash 500Mlx12", "PAK-CAR-007", unit, 33)
        cls.sticker = make("Stickers 12 ups", "PAK-LAB-237", unit, 1)
        cls.bom = cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.fg.product_tmpl_id.id, "product_qty": 12, "product_uom_id": unit.id,
            "consumption": "warning",
            "bom_line_ids": [
                Command.create({"product_id": cls.bulk.id, "product_qty": 6, "product_uom_id": litre.id}),
                Command.create({"product_id": cls.bottle.id, "product_qty": 12, "product_uom_id": unit.id}),
                Command.create({"product_id": cls.carton.id, "product_qty": 1, "product_uom_id": unit.id}),
                Command.create({"product_id": cls.sticker.id, "product_qty": 2, "product_uom_id": unit.id}),
            ]})
        Quant = cls.env["stock.quant"]
        for product, qty in [(cls.fg, 100), (cls.bulk, 500), (cls.bottle, 1000), (cls.carton, 50), (cls.sticker, 400)]:
            Quant._update_available_quantity(product, cls.stock, qty)
        cls.Line = cls.env["odin.stock.line"]
        cls.Book = cls.env["odin.stock.book"]

    def onhand(self, product):
        return self.Line._available(product, self.wh)[product.id]

    def test_filling_explodes_rounds_and_posts_an_mo(self):
        line = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 230})
        comps = {c.product_id: c.qty for c in line.component_ids}
        self.assertEqual(comps[self.bulk], 115.0, "liquids exact")
        self.assertEqual(comps[self.carton], 20.0, "19.17 cartons round up to whole cartons")
        self.assertEqual(comps[self.sticker], 39.0, "38.33 stickers round up")
        self.assertEqual(line._status()[0], "ready")
        bottles = line.component_ids.filtered(lambda c: c.product_id == self.bottle)
        bottles.qty = 232  # two broke while filling
        line.action_post()
        self.assertEqual(line.state, "posted")
        self.assertEqual(line.production_id.state, "done")
        self.assertTrue(line.ref.startswith("FIL/"))
        self.assertEqual(self.onhand(self.fg), 330)
        self.assertEqual(self.onhand(self.bulk), 385)
        self.assertEqual(self.onhand(self.bottle), 768)
        self.assertEqual(self.onhand(self.carton), 30)
        self.assertEqual(line.onhand_after, 330)
        with self.assertRaises(UserError):
            line.qty_in = 10

    def test_extra_and_removed_components(self):
        line = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 12})
        line.component_ids.filtered(lambda c: c.product_id == self.sticker).unlink()
        extra = self.env["product.product"].create({"name": "Shrink wrap", "is_storable": True, "type": "consu"})
        self.env["stock.quant"]._update_available_quantity(extra, self.stock, 10)
        self.Book.book_component(line.id, product_id=extra.id, qty=2)
        line.action_post()
        self.assertEqual(self.onhand(self.sticker), 400, "removed from the line: not consumed")
        self.assertEqual(self.onhand(extra), 8, "added to the line: consumed")

    def test_short_blocks_unless_forced(self):
        line = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 1200})  # 100 cartons, 50 on hand
        key, label, tone, detail = line._status()
        self.assertEqual(key, "short")
        self.assertIn("Type G", detail, "every short item is named in the detail")
        with self.assertRaises(UserError):
            line.action_post()
        line.action_post(allow_short=True)
        self.assertEqual(self.onhand(self.carton), -50)

    def test_sale_needs_customer_and_makes_a_delivery(self):
        line = self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 24})
        self.assertEqual(line._status()[0], "need_partner")
        line.partner_name = "naivas limited"
        self.assertEqual(line.partner_name, "NAIVAS LIMITED")
        line.action_post()
        self.assertEqual(line.picking_id.state, "done")
        self.assertEqual(line.picking_id.partner_id.name, "Naivas Limited")
        self.assertEqual(self.onhand(self.fg), 76)

    def test_wrong_column_and_return_receive_scrap_correction(self):
        typed_in = self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_in": 5, "partner_name": "x"})
        self.assertEqual((typed_in.qty_in, typed_in.qty_out), (0, 5), "a sale takes the quantity in Removed")
        ret = self.Line.create({"product_id": self.fg.id, "reason": "return", "qty_in": 6, "partner_name": "Highridge"})
        rec = self.Line.create({"product_id": self.bottle.id, "reason": "received", "qty_in": 100, "unit_cost": 14.5,
                                "partner_name": "Safepak"})
        scr = self.Line.create({"product_id": self.fg.id, "reason": "spoiled", "qty_out": 2})
        cor = self.Line.create({"product_id": self.sticker.id, "reason": "correction", "qty_out": 10})
        (ret | rec | scr | cor).action_post()
        self.assertEqual(self.onhand(self.fg), 104)
        self.assertEqual(self.onhand(self.bottle), 1100)
        self.assertEqual(self.onhand(self.sticker), 390)
        self.assertEqual(scr.scrap_id.state, "done")
        self.assertEqual(rec.ref.split("/")[0], "IN")

    def test_book_projection_and_sheet(self):
        self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 30, "partner_name": "A"})
        second = self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 80, "partner_name": "B"})
        book = self.Book.get_book()
        rows = {r["id"]: r for r in book["rows"]}
        self.assertEqual(rows[second.id]["onhand_after"], 100 - 30 - 80)
        self.assertEqual(rows[second.id]["status_key"], "short", "the first draft already took 30")
        self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 10, "partner_name": "C"}).action_post()
        sheet = self.Book.get_sheet()
        fg = next(r for g in sheet["groups"] for r in g["rows"] if r["product"]["id"] == self.fg.id)
        self.assertEqual(fg["closing"], 90)
        self.assertEqual(fg["removed"], 10)
        self.assertEqual(fg["opening"] + fg["added"] - fg["removed"], fg["closing"])

    def test_count_and_suggestions(self):
        self.Book.count_set(self.carton.id, 47)
        count = self.Book.get_count(group="counted")
        self.assertEqual(count["differences"], 1)
        self.assertEqual(count["rows"][0]["difference"], -3)
        self.Book.count_post()
        self.assertEqual(self.onhand(self.carton), 47)
        found = self.Book.book_suggest_products("aqua 500")
        self.assertEqual(found[0]["id"], self.fg.id)
        self.assertTrue(found[0]["has_recipe"])
        self.assertEqual(found[0]["usual_reason"], "filling")
