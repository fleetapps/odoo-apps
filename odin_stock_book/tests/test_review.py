from datetime import date, timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from .test_stock_book import TestStockBook


@tagged("post_install", "-at_install")
class TestStockBookReview(TestStockBook):
    """What the review found: each test is a way the book went wrong."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.storekeeper = new_test_user(cls.env, "storekeeper", groups="odin_stock_book.group_stock_book_user")
        # A supervisor who is not an Inventory administrator, as on site.
        cls.supervisor = new_test_user(cls.env, "supervisor", groups="odin_stock_book.group_stock_book_supervisor")

    # ------------------------------------------------------------ references
    def test_lines_posted_together_get_their_own_numbers(self):
        lines = self.Line.create([{"product_id": self.sticker.id, "reason": "correction", "qty_in": n}
                                  for n in range(1, 6)])
        lines.action_post()
        numbers = [int(ref.rsplit("/", 1)[1]) for ref in lines.sorted("id").mapped("ref")]
        self.assertEqual(len(set(lines.mapped("ref"))), 5, "five lines, five references")
        self.assertEqual(numbers, list(range(numbers[0], numbers[0] + 5)), "one after the other, no gaps")
        last_year = self.Line.create({"product_id": self.sticker.id, "reason": "correction", "qty_in": 1,
                                      "date": date(fields.Date.today().year - 1, 12, 30)})
        last_year.action_post()
        self.assertTrue(last_year.ref.startswith(f"ADJ/{last_year.date.year}/"), "numbered in the line's own year")

    # ------------------------------------------------------------ access
    def test_storekeeper_and_supervisor_open_every_view(self):
        for user in (self.storekeeper, self.supervisor):
            Book = self.Book.with_user(user)
            book = Book.get_book()
            self.assertAlmostEqual(book["stock_value"], 100 * 75 + 500 * 53 + 1000 * 15 + 50 * 33 + 400 * 1)
            self.assertTrue(Book.get_sheet()["groups"])
            self.assertTrue(Book.get_count()["rows"])

    def test_supervisor_posts_without_being_inventory_admin(self):
        Book = self.Book.with_user(self.supervisor)
        Line = self.Line.with_user(self.supervisor)
        sale = Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 2, "partner_name": "New Shop Ltd"})
        receipt = Line.create({"product_id": self.bottle.id, "reason": "received", "qty_in": 10, "unit_cost": 12,
                               "partner_name": "Safepak"})
        result = Book.book_post((sale | receipt).ids)
        self.assertEqual(result["count"], 2)
        self.assertEqual(sale.partner_id.name, "New Shop Ltd", "a new customer is created for the delivery")
        self.assertEqual(receipt.move_ids.sudo().value, 120, "the receipt carries the cost typed")
        Book.count_set(self.carton.id, 48)
        self.assertEqual(Book.count_post()["applied"], 1)
        self.assertEqual(self.onhand(self.carton), 48)

    def test_posted_lines_stay_posted(self):
        line = self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 3, "partner_name": "A"})
        line.action_post()
        as_storekeeper = line.with_user(self.storekeeper)
        for vals in ({"state": "draft"}, {"ref": "OUT/2000/0001"}, {"move_ids": [Command.clear()]}):
            with self.assertRaises(UserError, msg=vals):
                as_storekeeper.write(vals)
        with self.assertRaises(UserError):
            as_storekeeper.partner_name = "Someone else"
        with self.assertRaises(UserError):
            self.Line.with_user(self.storekeeper).create({"product_id": self.fg.id, "reason": "correction",
                                                          "qty_in": 5, "state": "posted"})
        self.assertEqual(line.state, "posted")
        filling = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 12})
        filling.action_post()
        with self.assertRaises(UserError):
            filling.component_ids[:1].unlink()
        with self.assertRaises(UserError):
            self.env["odin.stock.line.component"].create({"line_id": filling.id, "product_id": self.sticker.id})
        draft = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 12})
        with self.assertRaises(UserError):
            self.Book.book_component(draft.id, component_id=filling.component_ids[0].id, remove=True)
        self.assertEqual(len(filling.component_ids), 4)

    # ------------------------------------------------------------ count
    def test_count_after_stock_moved_asks_which_to_keep(self):
        self.Book.count_set(self.fg.id, 95)  # 100 on the system: 5 missing
        self.env.flush_all()  # counted, then a sale is posted
        self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 10, "partner_name": "A"}).action_post()
        result = self.Book.count_post()
        self.assertEqual(result["applied"], 0, "nothing applied behind the supervisor's back")
        self.assertEqual(result["moved"][0]["then"], 100)
        self.assertEqual(result["moved"][0]["now"], 90)
        self.assertTrue(self.Book.get_count(group="counted")["rows"][0]["moved"])
        self.Book.count_post(resolve="difference")
        self.assertEqual(self.onhand(self.fg), 85, "the 5 found missing come off what is there now")

    def test_count_keeps_counted_quantity_when_asked(self):
        self.Book.count_set(self.fg.id, 95)
        self.env.flush_all()
        self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 10, "partner_name": "A"}).action_post()
        self.Book.count_post(resolve="counted")
        self.assertEqual(self.onhand(self.fg), 95)

    def test_count_with_stock_on_a_shelf_below_the_stock_location(self):
        shelf = self.env["stock.location"].create({"name": "Shelf A", "location_id": self.stock.id})
        self.env["stock.quant"]._update_available_quantity(self.carton, shelf, 10)  # 50 + 10 = 60
        self.Book.count_set(self.carton.id, 55)
        row = self.Book.get_count(group="counted")["rows"][0]
        self.assertEqual((row["system"], row["counted"], row["difference"]), (60, 55, -5))
        self.Book.count_post()
        self.assertEqual(self.onhand(self.carton), 55, "the warehouse ends at the count")

    def test_tracked_items_are_counted_in_inventory(self):
        self.carton.tracking = "lot"
        with self.assertRaises(UserError):
            self.Book.count_set(self.carton.id, 3)

    # ------------------------------------------------------------ stock sheet
    def test_sheet_shows_what_was_really_used(self):
        line = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 230})
        line.component_ids.filtered(lambda c: c.product_id == self.bottle).qty = 232  # two broke
        line.action_post()
        rows = {r["product"]["id"]: r for g in self.Book.get_sheet()["groups"] for r in g["rows"]}
        bottle = rows[self.bottle.id]
        self.assertEqual((bottle["opening"], bottle["removed"], bottle["closing"]), (1000, 232, 768))

    def test_sheet_counts_transfers_to_another_warehouse(self):
        other = self.env["stock.warehouse"].create({"name": "Branch", "code": "BR2"})
        move = self.env["stock.move"].create({
            "product_id": self.fg.id, "product_uom_qty": 7, "product_uom": self.fg.uom_id.id,
            "location_id": self.stock.id, "location_dest_id": other.lot_stock_id.id,
        })
        move._action_confirm()
        move._set_quantity_done(7)
        move.picked = True
        move._action_done()
        rows = {r["product"]["id"]: r for g in self.Book.get_sheet()["groups"] for r in g["rows"]}
        self.assertEqual((rows[self.fg.id]["removed"], rows[self.fg.id]["closing"]), (7, 93))
        branch = {r["product"]["id"]: r for g in self.Book.get_sheet(warehouse_id=other.id)["groups"]
                  for r in g["rows"]}
        self.assertEqual(branch[self.fg.id]["added"], 7)

    # ------------------------------------------------------------ recipes
    def test_recipe_written_in_another_unit(self):
        kg, gram = self.env.ref("uom.product_uom_kgm"), self.env.ref("uom.product_uom_gram")
        powder = self.env["product.product"].create({"name": "Fragrance powder", "is_storable": True,
                                                      "uom_id": kg.id})
        self.env["stock.quant"]._update_available_quantity(powder, self.stock, 5)
        soap = self.env["product.product"].create({"name": "Soap bar", "is_storable": True})
        self.env["mrp.bom"].create({"product_tmpl_id": soap.product_tmpl_id.id, "product_qty": 1, "bom_line_ids": [
            Command.create({"product_id": powder.id, "product_qty": 50, "product_uom_id": gram.id})]})
        line = self.Line.create({"product_id": soap.id, "reason": "production", "qty_in": 10})
        self.assertAlmostEqual(line.component_ids.qty, 0.5, msg="500 g shown as 0.5 kg")
        line.action_post()
        self.assertAlmostEqual(self.onhand(powder), 4.5, msg="0.5 kg used, not 500 kg")

    def test_kit_components_are_what_the_order_consumes(self):
        cap = self.env["product.product"].create({"name": "Cap", "is_storable": True})
        pump = self.env["product.product"].create({"name": "Pump", "is_storable": True})
        closure = self.env["product.product"].create({"name": "Closure kit", "is_storable": True})
        for p in (cap, pump):
            self.env["stock.quant"]._update_available_quantity(p, self.stock, 100)
        self.env["mrp.bom"].create({"product_tmpl_id": closure.product_tmpl_id.id, "type": "phantom", "bom_line_ids": [
            Command.create({"product_id": cap.id, "product_qty": 1}),
            Command.create({"product_id": pump.id, "product_qty": 1})]})
        self.bom.bom_line_ids = [Command.create({"product_id": closure.id, "product_qty": 12})]
        line = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 12})
        products = line.component_ids.product_id
        self.assertIn(cap, products)
        self.assertNotIn(closure, products, "the kit is opened up, as the order will")
        line.action_post()
        self.assertEqual(self.onhand(cap), 88)
        self.assertEqual(self.onhand(closure), 0)

    def test_consumable_component_never_short(self):
        water = self.env["product.product"].create({"name": "Water", "type": "consu", "is_storable": False})
        self.bom.bom_line_ids = [Command.create({"product_id": water.id, "product_qty": 3})]
        line = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 12})
        self.assertEqual(line._status()[0], "ready", "Odoo keeps no stock of it, so it is never short")

    # ------------------------------------------------------------ statuses
    def test_lines_the_book_cannot_post_say_so_first(self):
        tomorrow = self.Line.create({"product_id": self.fg.id, "reason": "spoiled", "qty_out": 1,
                                     "date": fields.Date.today() + timedelta(days=1)})
        self.assertEqual(tomorrow._status()[0], "future")
        self.fg.tracking = "lot"
        tracked = self.Line.create({"product_id": self.fg.id, "reason": "spoiled", "qty_out": 1})
        self.assertEqual(tracked._status()[0], "tracked")
        self.assertEqual(self.Book.book_post((tomorrow | tracked).ids)["count"], 0)

    # ------------------------------------------------------------ dates
    def test_back_dated_line_lands_on_its_day(self):
        day = fields.Date.today() - timedelta(days=20)
        sale = self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 4, "partner_name": "A",
                                 "date": day})
        filling = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 12, "date": day})
        (sale | filling).action_post()
        self.assertEqual(sale.picking_id.date_done.date(), day)
        self.assertEqual(sale.move_ids.date.date(), day)
        self.assertEqual(filling.production_id.date_finished.date(), day)

    # ------------------------------------------------------------ the page's helpers
    def test_quantity_goes_to_the_reasons_column(self):
        row = self.Book.book_save(False, {"product_id": self.fg.id, "reason": "sale", "qty_in": 6,
                                          "partner_name": "A"})
        self.assertEqual((row["qty_in"], row["qty_out"], row["moved_to"]), (0, 6, "removed"))
        row = self.Book.book_save(row["id"], {"reason": "return"})
        self.assertEqual((row["qty_in"], row["qty_out"]), (6, 0))

    def test_paste_rows_from_the_sheet(self):
        text = ("DATE\tITEM\tADDED\tREMOVED\tREASON\tCUSTOMER\n"
                "3/11\tFIN-PAK-002\t\t24\tSold\tnaivas\n"
                "\taqua blue 500\t\t6\tDamaged\t\n"         # no date: the row above's
                "4 Nov\tStickers 12 ups\t1,000\t\tPurchase\tSafepak\n"
                "5/11\tSomething else\t3\t\tReceived\t\n"   # no such item
                "5/11\tPAK-BOT-014\t\t\tSale\tX\n")          # no quantity
        result = self.Book.book_paste(text, 2026, 11)
        self.assertEqual(result["created"], 3)
        self.assertEqual([s["row"] for s in result["skipped"]], [5, 6])
        lines = self.Line.search([], order="id desc", limit=3).sorted("id")
        self.assertEqual(lines.mapped("reason"), ["sale", "spoiled", "received"])
        self.assertEqual(lines.mapped("date"), [date(2026, 11, 3), date(2026, 11, 3), date(2026, 11, 4)])
        self.assertEqual(lines[0].partner_name, "NAIVAS")
        self.assertEqual(lines[2].qty_in, 1000)
        no_heading = self.Book.book_paste("7\tFIN-PAK-002\t\t2\tSale\tA", 2026, 11)
        self.assertEqual(no_heading["created"], 1)

    def test_reverse_and_copy(self):
        sale = self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 5, "partner_name": "A"})
        sale.action_post()
        back = self.Line.browse(self.Book.book_reverse(sale.id))
        self.assertEqual((back.reason, back.qty_in, back.partner_name, back.reverses_id), ("return", 5, "A", sale))
        with self.assertRaises(UserError):
            self.Book.book_reverse(sale.id)
        row = next(r for r in self.Book.get_book()["rows"] if r["id"] == sale.id)
        self.assertFalse(row["can_reverse"])
        filling = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 24})
        filling.component_ids.filtered(lambda c: c.product_id == self.bottle).qty = 30
        again = self.Line.browse(self.Book.book_copy(filling.id))
        self.assertEqual((again.state, again.qty_in), ("draft", 24))
        self.assertEqual(again.component_ids.filtered(lambda c: c.product_id == self.bottle).qty, 24,
                         "a copy takes the recipe again")

    def test_partner_names_stay_in_their_company(self):
        other = self.env["res.company"].create({"name": "Other Co"})
        other_wh = self.env["stock.warehouse"].search([("company_id", "=", other.id)], limit=1)
        self.Line.create({"company_id": other.id, "warehouse_id": other_wh.id, "partner_name": "Zebra Secret",
                          "product_id": self.fg.id, "reason": "sale", "qty_out": 1})
        self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 1, "partner_name": "Zebra Mine"})
        names = self.Book.with_user(self.storekeeper).book_suggest_partners("zebra")
        self.assertIn("ZEBRA MINE", names)
        self.assertNotIn("ZEBRA SECRET", names)


@tagged("post_install", "-at_install")
class TestStockBookPage(TestStockBook):
    """What the page's notices and printing rely on."""

    def test_short_status_fits_its_column(self):
        line = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 720})  # 60 cartons, 50 on hand
        key, label, _tone, detail = line._status()
        self.assertEqual((key, label), ("short", "Short · 1 item"))
        self.assertIn("Type G", detail, "the names are in the detail")
        line.qty_in = 1200  # 100 cartons, 1200 bottles, 600 L of bulk: all three short
        self.assertEqual(line._status()[1], "Short · 3 items")

    def test_undo_the_quantity_moving_column(self):
        row = self.Book.book_save(False, {"product_id": self.fg.id, "reason": "sale", "qty_in": 6, "partner_name": "A"})
        self.assertEqual(row["moved_to"], "removed")
        row = self.Book.book_save(row["id"], {"qty_in": 6, "qty_out": 0}, keep_column=True)
        self.assertEqual((row["qty_in"], row["qty_out"], row["status_key"]), (6, 0, "wrong_way"),
                         "back where it was typed: the line asks which it is")
        row = self.Book.book_save(row["id"], {"reason": "return"})
        self.assertEqual((row["qty_in"], row["status_key"]), (6, "ready"))

    def test_undo_cancel_and_undo_added_lines(self):
        line = self.Line.create({"product_id": self.fg.id, "reason": "spoiled", "qty_out": 1})
        self.Book.book_cancel([line.id])
        self.assertEqual(self.Book.book_restore([line.id]), 1)
        self.assertEqual(line.state, "draft")
        posted = self.Line.create({"product_id": self.fg.id, "reason": "spoiled", "qty_out": 1})
        posted.action_post()
        posted.action_draft()
        self.assertEqual(posted.state, "posted", "only a cancelled line goes back to draft")
        pasted = self.Book.book_paste("3\tFIN-PAK-002\t\t2\tSale\tA\n3\tFIN-PAK-002\t\t1\tSale\tB", 2026, 11)
        self.assertEqual(len(pasted["ids"]), 2)
        first = self.Line.browse(pasted["ids"][0])
        first.date = fields.Date.today()
        first.action_post()
        result = self.Book.book_discard(pasted["ids"])
        self.assertEqual(result, {"removed": 1, "kept": 1}, "a line posted meanwhile stays")
        self.assertTrue(first.exists())

    def test_print_a_day_and_a_month(self):
        today = fields.Date.today()
        sale = self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 24, "partner_name": "Naivas"})
        sale.action_post()
        self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 12, "note": "batch 2"})
        cancelled = self.Line.create({"product_id": self.sticker.id, "reason": "spoiled", "qty_out": 3,
                                      "partner_name": "SHOULD NOT PRINT"})
        cancelled.action_cancel()
        action = self.Book.book_print(today.year, today.month)
        self.assertEqual((action["type"], action["report_name"]), ("ir.actions.report", "odin_stock_book.report_movement_log"))
        Report = self.env["ir.actions.report"]
        html = Report._render_qweb_html("odin_stock_book.report_movement_log", [], data=action["data"])[0].decode()
        self.assertIn(sale.ref, html)
        self.assertIn("NAIVAS", html)
        self.assertIn("batch 2", html)
        self.assertIn("Not posted", html)
        self.assertIn("not posted yet", html, "a sheet with drafts says it is not final")
        self.assertIn("500 Ml Handwash Round", html, "a filling prints its components")
        self.assertNotIn("SHOULD NOT PRINT", html)
        self.assertIn("Checked by (supervisor)", html)
        day = Report._render_qweb_html("odin_stock_book.report_movement_log", [], data=self.Book.book_print(
            day=fields.Date.to_string(today - timedelta(days=1)))["data"])[0].decode()
        self.assertNotIn(sale.ref, day, "a day prints only that day")
        self.assertIn("No movements.", day)
