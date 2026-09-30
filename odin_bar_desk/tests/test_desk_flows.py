from datetime import date

import psycopg2

from odoo import Command
from odoo.exceptions import ConcurrencyError, UserError
from odoo.tests import freeze_time, tagged
from odoo.tools import mute_logger

from .common import BarDeskCase

DAY1 = date(2026, 9, 27)  # a Sunday


@tagged("post_install", "-at_install")
class TestDeskFlows(BarDeskCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env["res.partner"].create({"name": "EABL", "is_company": True})

    def setUp(self):
        super().setUp()
        self.opening_stock(self.loc_store, [(self.jameson, 1000), (self.gordons, 500), (self.tusker, 480)])
        self.opening_stock(self.loc_be, [(self.jameson, 100), (self.tusker, 48)])
        self.token = self.controller_token()
        self.store_token = self.login(self.device_store, self.store, self.sam, "4321")

    def move(self, source, lines, to=None, reason=None, request=None, **kwargs):
        result = self.desk(self.controller).desk_move(
            self.store.id,
            self.token,
            request or self.uuid(),
            source.id,
            lines,
            to_bar_id=to.id if to else False,
            reason_id=reason.id if reason else False,
            **kwargs,
        )
        return self.env["odin.bar.activity"].browse(result["activity_id"]).picking_ids

    # ------------------------------------------------------------------
    # Moves
    # ------------------------------------------------------------------

    def test_moves_between_locations_use_the_bars_operation_types(self):
        lines = [{"product_id": self.jameson.id, "uom_id": self.uom_750.id, "qty": 2}]
        issue = self.move(self.store, lines, to=self.bar_be)
        back = self.move(self.bar_be, [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": 1}], to=self.store)
        across = self.move(self.bar_be, [{"product_id": self.jameson.id, "uom_id": self.uom_tot.id, "qty": 10}], to=self.bar_bb)
        self.assertEqual(issue.picking_type_id, self.type_iss_be)
        self.assertEqual(back.picking_type_id, self.type_ret_be)
        self.assertEqual(across.picking_type_id, self.type_ibt)
        for picking in issue | back | across:
            self.assertEqual(picking.state, "done")
            self.assertTrue(picking.bar_manual_move)
            self.assertEqual(picking.bar_employee_id, self.kim)
            self.assertFalse(picking.bar_business_date)
        self.assertEqual(across.location_id, self.loc_be)
        self.assertEqual(across.location_dest_id, self.loc_bb)
        self.assertEqual(self.qty(self.jameson, self.loc_be), 100 + 50 - 10)
        self.assertEqual(self.qty(self.jameson, self.loc_bb), 10)
        self.assertEqual(self.qty(self.tusker, self.loc_store), 480 + 24)

    def test_moves_out_of_the_club(self):
        roma = self.move(
            self.bar_be, [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 6}], reason=self.reasons["roma"]
        )
        self.assertEqual(roma.picking_type_id, self.type_roma)
        self.assertEqual(roma.location_dest_id, self.loc_roma)
        self.assertEqual(roma.bar_reason_id, self.reasons["roma"])
        lines = [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 2}]
        with self.assertRaisesRegex(UserError, "member"):
            self.move(self.bar_be, lines, reason=self.reasons["debt"])
        debt = self.move(self.bar_be, lines, reason=self.reasons["debt"], member_ref=" M-1024 ")
        self.assertEqual(debt.bar_member_ref, "M-1024")
        self.assertEqual(self.qty(self.tusker, self.loc_be), 40)
        # Only "issue out" reasons are destinations: bar-to-bar is a location.
        with self.assertRaisesRegex(UserError, "where the stock went"):
            self.move(self.bar_be, lines, reason=self.reasons["transfer"])

    def test_a_move_can_belong_to_the_day_being_closed(self):
        with freeze_time("2026-09-28 07:00:00"):  # 10:00, the morning after
            picking = self.move(
                self.store,
                [{"product_id": self.jameson.id, "uom_id": self.uom_750.id, "qty": 2}],
                to=self.bar_be,
                business_date=str(DAY1),
            )
        self.assertEqual(picking.bar_business_date, DAY1)
        # It is not a POS sale of that day.
        self.assertEqual(self.bar_be._pos_moved_qty(self.jameson, DAY1)[self.jameson.id], 0)

    def test_no_move_into_an_approved_day(self):
        self.bar_be._mark_pos_posted(DAY1, no_sales=True)
        with freeze_time("2026-09-28 07:00:00"):
            count = self.submit_count(self.controller, self.bar_be, self.kim, "9999", [], DAY1)
            self.approve(count)
            lines = [{"product_id": self.jameson.id, "uom_id": self.uom_750.id, "qty": 1}]
            with self.assertRaisesRegex(UserError, "already approved at Bulls Eye"):
                self.move(self.store, lines, to=self.bar_be, business_date=str(DAY1))
            # On today it is fine.
            self.assertTrue(self.move(self.store, lines, to=self.bar_be))

    def test_moves_refuse_bad_lines(self):
        with self.assertRaisesRegex(UserError, "not offered"):
            self.move(self.bar_be, [{"product_id": self.not_on_desk.id, "qty": 1}], to=self.store)
        with self.assertRaisesRegex(UserError, "not a unit"):
            self.move(
                self.bar_be, [{"product_id": self.tusker.id, "uom_id": self.uom_750.id, "qty": 1}], to=self.store
            )
        with self.assertRaisesRegex(UserError, "negative"):
            self.move(self.bar_be, [{"product_id": self.tusker.id, "qty": -1}], to=self.store)
        with self.assertRaisesRegex(UserError, "at least one"):
            self.move(self.bar_be, [{"product_id": self.tusker.id, "qty": 0}], to=self.store)
        with self.assertRaisesRegex(UserError, "another location"):
            self.move(self.bar_be, [{"product_id": self.tusker.id, "qty": 1}], to=self.bar_be)

    # ------------------------------------------------------------------
    # Retries
    # ------------------------------------------------------------------

    def test_retry_with_the_same_request_id_posts_once(self):
        request = self.uuid()
        lines = [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 3}]
        first = self.move(self.bar_be, lines, to=self.store, request=request)
        second = self.move(self.bar_be, lines, to=self.store, request=request)
        self.assertEqual(first, second)
        self.assertEqual(self.qty(self.tusker, self.loc_be), 45)
        self.assertEqual(self.env["odin.bar.activity"].search_count([("uuid", "=", request)]), 1)

    def test_retry_of_a_count(self):
        desk = self.desk(self.controller)
        count = desk.desk_count_start(self.bar_be.id, self.token)
        request = self.uuid()
        lines = [self.count_line(self.jameson, open_tots=100), self.count_line(self.gordons), self.count_line(self.tusker, units=48)]
        first = desk.desk_count_submit(self.bar_be.id, self.token, request, count["id"], lines)
        second = desk.desk_count_submit(self.bar_be.id, self.token, request, count["id"], lines)
        self.assertEqual(first["activity_id"], second["activity_id"])

    def test_a_request_id_is_unique_in_the_database(self):
        request = self.uuid()
        activity = self.env["odin.bar.desk"]._desk_begin(request, {"kind": "move", "bar_id": self.bar_be.id})
        with self.assertRaises(ConcurrencyError), mute_logger("odoo.sql_db"):
            self.env["odin.bar.desk"]._desk_begin(request, {"kind": "move", "bar_id": self.bar_be.id})
        self.assertEqual(self.env["odin.bar.desk"]._desk_replay(request), activity)
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self.env["odin.bar.activity"].create({"kind": "move", "bar_id": self.bar_be.id, "uuid": request})
            self.env.flush_all()

    # ------------------------------------------------------------------
    # Supplier deliveries at the store
    # ------------------------------------------------------------------

    def test_receive_from_a_supplier(self):
        desk = self.desk(self.device_store)
        self.assertIn(self.supplier.id, [p["id"] for p in desk.desk_suppliers(self.store.id, self.store_token, "EABL")])
        result = desk.desk_supplier_delivery(
            self.store.id,
            self.store_token,
            self.uuid(),
            self.supplier.id,
            [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "invoiced": 5, "received": 5}],
        )
        receipt = self.env["odin.bar.activity"].browse(result["activity_id"]).picking_ids
        self.assertEqual(receipt.state, "done")
        self.assertEqual(receipt.picking_type_code, "incoming")
        self.assertEqual(receipt.partner_id, self.supplier)
        self.assertEqual(receipt.location_dest_id, self.loc_store)
        self.assertEqual(self.qty(self.tusker, self.loc_store), 600)

    def test_validate_a_waiting_receipt_with_a_shortfall(self):
        receipt = self.env["stock.picking"].create(
            {
                "picking_type_id": self.store.receipt_type_id.id,
                "partner_id": self.supplier.id,
                "origin": "P00042",
                "move_ids": [
                    Command.create(
                        {"product_id": self.gordons.id, "product_uom": self.uom_750.id, "product_uom_qty": 12}
                    )
                ],
            }
        )
        receipt.action_confirm()
        desk = self.desk(self.device_store)
        self.assertEqual([r["id"] for r in desk.desk_receipts(self.store.id, self.store_token)], receipt.ids)
        detail = desk.desk_receipt(self.store.id, self.store_token, receipt.id)
        self.assertEqual(detail["lines"][0]["qty"], 12)
        desk.desk_receipt_validate(
            self.store.id, self.store_token, self.uuid(), receipt.id, {receipt.move_ids.id: 10}
        )
        self.assertEqual(receipt.state, "done")
        self.assertEqual(self.qty(self.gordons, self.loc_store), 500 + 250)
        backorder = self.env["stock.picking"].search([("backorder_id", "=", receipt.id)])
        self.assertEqual(backorder.move_ids.product_uom_qty, 2)

    def test_supplier_deliveries_go_to_the_store_only(self):
        token = self.login(self.device_be, self.bar_be, self.mary, "1234")
        with self.assertRaisesRegex(UserError, "store"):
            self.desk(self.device_be).desk_receipts(self.bar_be.id, token)

    # ------------------------------------------------------------------
    # Day sheet and catalogue
    # ------------------------------------------------------------------

    def test_day_sheet(self):
        with freeze_time("2026-09-28 07:00:00"):  # 10:00 on Monday: closing Sunday
            self.bar_be._mark_pos_posted(DAY1, no_sales=True)
            self.move(
                self.store,
                [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": 1}],
                to=self.bar_be,
                business_date=str(DAY1),
            )
            home = self.desk(self.controller).desk_home(self.store.id, self.token)
        self.assertEqual(home["day"], {"date": "2026-09-27", "label": "Sun 27 Sep", "is_today": False})
        self.assertEqual([day["label"] for day in home["days"]], ["Fri 25 Sep", "Sat 26 Sep", "Yesterday", "Today"])
        self.assertEqual([loc["code"] for loc in home["locations"]], ["MS", "BB", "BE"])
        pos = {loc["code"]: loc["pos"] for loc in home["locations"]}
        self.assertEqual(pos, {"MS": "none", "BE": "posted", "BB": "missing"})
        self.assertEqual(len(home["moves"]), 1)
        self.assertEqual(home["moves"][0]["from"], "Main Store")
        self.assertEqual(home["moves"][0]["to"], "Bulls Eye")
        self.assertEqual(home["moves"][0]["who"], "Kim")
        self.assertEqual(home["counted"], 0)
        self.assertFalse(home["can_approve"])
        self.assertIn("Banda Bar", home["approve_blocked"])
        self.assertEqual(home["store_id"], self.store.id)

    def test_catalog(self):
        catalog = self.desk(self.controller).desk_catalog(self.store.id, self.token)
        products = {product["id"]: product for product in catalog["products"]}
        self.assertNotIn(self.not_on_desk.id, products)
        jameson = products[self.jameson.id]
        self.assertTrue(jameson["poured"])
        self.assertEqual([uom["id"] for uom in jameson["bottles"]], [self.uom_750.id, self.uom_1l.id])
        self.assertEqual([uom["factor"] for uom in jameson["bottles"]], [25.0, 33.0])
        tusker = products[self.tusker.id]
        self.assertFalse(tusker["poured"])
        self.assertEqual([uom["id"] for uom in tusker["packs"]], [self.uom_crate.id])
        self.assertEqual([loc["code"] for loc in catalog["locations"]], ["MS", "BB", "BE"])
        self.assertEqual({d["name"] for d in catalog["destinations"]}, {"Roma", "Event", "Unpaid bill"})
        self.assertEqual(
            [reason["code"] for reason in catalog["variance_reasons"]],
            ["breakage", "spillage", "complimentary", "staff", "missed_move", "miscount", "pos_error", "unexplained"],
        )

    def test_count_sheet_follows_the_location_setting(self):
        self.bar_bb.sheet_product_ids = [Command.set([self.tusker.id, self.jameson.id])]
        count = self.desk(self.controller).desk_count_start(self.bar_bb.id, self.token)
        self.assertEqual({line["product_id"] for line in count["lines"]}, {self.tusker.id, self.jameson.id})

    def test_defaults_map_to_the_existing_setup(self):
        self.assertEqual(self.bar_be.issue_type_id, self.type_iss_be)
        self.assertEqual(self.bar_be.return_type_id, self.type_ret_be)
        self.assertEqual(self.bar_be.sale_type_id, self.type_sal_be)
        self.assertEqual(self.bar_be.store_id, self.store)
        self.assertEqual(self.store.receipt_type_id, self.warehouse.in_type_id)
        self.assertEqual(self.reasons["event"].picking_type_id, self.type_evt)
        self.assertEqual(self.reasons["transfer"].picking_type_id, self.type_ibt)
        self.assertEqual(set(self.reasons), {"roma", "event", "debt", "transfer"})
        self.assertFalse(self.bar_be.setup_issues)
        self.assertEqual(self.jameson.bar_bottle_uom_id, self.uom_750)
        self.assertEqual(self.jameson.tots_per_bottle, 25)
        self.assertFalse(self.tusker.bar_bottle_uom_id)
