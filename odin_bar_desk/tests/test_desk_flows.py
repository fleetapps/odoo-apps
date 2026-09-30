import psycopg2

from odoo import Command
from odoo.exceptions import ConcurrencyError, UserError
from odoo.tests import freeze_time, tagged
from odoo.tools import mute_logger

from .common import BarDeskCase


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
        self.be_token = self.login(self.device_be, self.bar_be, self.mary, "1234")
        self.bb_token = self.login(self.device_bb, self.bar_bb, self.john, "5678")
        self.store_token = self.login(self.device_store, self.store, self.sam, "4321")

    def stock_out(self, reason_code, lines, request=None, **kwargs):
        return self.desk(self.device_be).desk_stock_out(
            self.bar_be.id, self.be_token, request or self.uuid(), self.reasons[reason_code].id, lines, **kwargs
        )

    def send_to_be(self, lines):
        result = self.desk(self.device_store).desk_store_send(
            self.store.id, self.store_token, self.uuid(), self.bar_be.id, lines
        )
        return self.env["odin.bar.activity"].browse(result["activity_id"]).picking_ids

    # ------------------------------------------------------------------
    # Retries
    # ------------------------------------------------------------------

    def test_retry_with_the_same_request_id_posts_once(self):
        lines = [{"product_id": self.jameson.id, "uom_id": self.uom_tot.id, "qty": 2}]
        request = self.uuid()
        first = self.stock_out("roma", lines, request)
        second = self.stock_out("roma", lines, request)
        self.assertEqual(first, second)
        self.assertEqual(self.env["odin.bar.activity"].search_count([("uuid", "=", request)]), 1)
        self.assertEqual(
            self.env["stock.picking"].search_count([("bar_activity_id", "=", first["activity_id"])]), 1
        )
        self.assertEqual(self.qty(self.jameson, self.loc_be), 98)

    def test_retry_of_a_count_and_of_a_delivery_check(self):
        desk_be = self.desk(self.device_be)
        count = desk_be.desk_count_start(self.bar_be.id, self.be_token, kind="spot")
        lines = [self.count_line(self.jameson, open_tots=99)]
        request = self.uuid()
        first = desk_be.desk_count_submit(self.bar_be.id, self.be_token, request, count["id"], lines)
        second = desk_be.desk_count_submit(self.bar_be.id, self.be_token, request, count["id"], lines)
        self.assertEqual(first, second)
        self.assertEqual(self.env["odin.bar.activity"].search_count([("count_id", "=", count["id"])]), 1)

        delivery = self.send_to_be([{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": 1}])
        request = self.uuid()
        received = {delivery.move_ids.id: 20}
        first = desk_be.desk_delivery_check(self.bar_be.id, self.be_token, request, delivery.id, received)
        second = desk_be.desk_delivery_check(self.bar_be.id, self.be_token, request, delivery.id, received)
        self.assertEqual(first, second)
        self.assertEqual(len(delivery.bar_dispute_ids), 1)

    def test_a_request_id_is_unique_in_the_database(self):
        request = self.uuid()
        activity = self.env["odin.bar.activity"].create({"kind": "ack", "bar_id": self.bar_be.id, "uuid": request})
        # A concurrent duplicate cannot see the first row yet: it hits the
        # unique index and is retried in a fresh transaction, which replays.
        with self.assertRaises(ConcurrencyError), mute_logger("odoo.sql_db"):
            self.env["odin.bar.desk"]._desk_begin(request, {"kind": "ack", "bar_id": self.bar_be.id})
        self.assertEqual(self.env["odin.bar.desk"]._desk_replay(request), activity)
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self.env["odin.bar.activity"].create({"kind": "ack", "bar_id": self.bar_bb.id, "uuid": request})

    # ------------------------------------------------------------------
    # Stock out
    # ------------------------------------------------------------------

    def test_issue_out_reasons_post_validated_transfers(self):
        result = self.stock_out(
            "roma",
            [
                {"product_id": self.jameson.id, "uom_id": self.uom_750.id, "qty": 1},
                {"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 6},
            ],
        )
        picking = self.env["odin.bar.activity"].browse(result["activity_id"]).picking_ids
        self.assertEqual(picking.state, "done")
        self.assertEqual(picking.picking_type_id, self.type_roma)
        self.assertEqual(picking.location_id, self.loc_be)
        self.assertEqual(picking.location_dest_id, self.loc_roma)
        self.assertEqual(picking.bar_employee_id, self.mary)
        self.assertEqual(picking.bar_reason_id, self.reasons["roma"])
        self.assertEqual(self.qty(self.jameson, self.loc_be), 75)
        self.assertEqual(self.qty(self.tusker, self.loc_be), 42)
        activity = self.env["odin.bar.activity"].browse(result["activity_id"])
        self.assertAlmostEqual(activity.amount, 25 * 80.0 + 6 * 150.0)

    def test_unpaid_bill_needs_a_member(self):
        lines = [{"product_id": self.jameson.id, "uom_id": self.uom_tot.id, "qty": 3}]
        with self.assertRaisesRegex(UserError, "member"):
            self.stock_out("debt", lines)
        result = self.stock_out("debt", lines, member_ref="M1234 Otieno")
        picking = self.env["odin.bar.activity"].browse(result["activity_id"]).picking_ids
        self.assertEqual(picking.picking_type_id, self.type_debt)
        self.assertEqual(picking.bar_member_ref, "M1234 Otieno")
        self.assertIn("M1234 Otieno", picking.origin)

    def test_scrap_reasons_post_scraps(self):
        result = self.stock_out("breakage", [{"product_id": self.jameson.id, "uom_id": self.uom_1l.id, "qty": 1}])
        scrap = self.env["odin.bar.activity"].browse(result["activity_id"]).scrap_ids
        self.assertEqual(scrap.state, "done")
        self.assertEqual(scrap.location_id, self.loc_be)
        self.assertEqual(scrap.scrap_reason_tag_ids.name, "Breakage")
        self.assertEqual(scrap.bar_employee_id, self.mary)
        self.assertEqual(self.qty(self.jameson, self.loc_be), 67)

    def test_back_to_store(self):
        result = self.stock_out("store", [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 12}])
        picking = self.env["odin.bar.activity"].browse(result["activity_id"]).picking_ids
        self.assertEqual(picking.picking_type_id, self.type_ret_be)
        self.assertEqual(picking.location_dest_id, self.loc_store)
        self.assertEqual(picking.state, "done")
        self.assertEqual(self.qty(self.tusker, self.loc_store), 492)

    def test_transfer_to_another_bar_shows_in_its_deliveries(self):
        lines = [{"product_id": self.jameson.id, "uom_id": self.uom_tot.id, "qty": 10}]
        with self.assertRaisesRegex(UserError, "Pick the bar"):
            self.stock_out("transfer", lines)
        with self.assertRaisesRegex(UserError, "Pick the bar"):
            self.stock_out("transfer", lines, dest_bar_id=self.bar_be.id)
        result = self.stock_out("transfer", lines, dest_bar_id=self.bar_bb.id)
        picking = self.env["odin.bar.activity"].browse(result["activity_id"]).picking_ids
        self.assertEqual(picking.picking_type_id, self.type_ibt)
        self.assertEqual(picking.location_dest_id, self.loc_bb)
        self.assertEqual(picking.bar_ack_state, "not_checked")
        deliveries = self.desk(self.device_bb).desk_deliveries(self.bar_bb.id, self.bb_token)
        self.assertEqual([delivery["id"] for delivery in deliveries], picking.ids)
        self.assertEqual(deliveries[0]["source"], "Bulls Eye")
        home = self.desk(self.device_bb).desk_home(self.bar_bb.id, self.bb_token)
        self.assertEqual(home["unchecked"], 1)
        self.assertIn("Transfer from Bulls Eye", [item["title"] for item in home["timeline"]])

    def test_stock_out_refuses_bad_lines(self):
        cases = [
            ([], "at least one item"),
            ([{"product_id": self.not_on_desk.id, "qty": 1}], "not offered"),
            ([{"product_id": self.jameson.id, "uom_id": self.uom_crate.id, "qty": 1}], "not a unit"),
            ([{"product_id": self.jameson.id, "uom_id": self.uom_tot.id, "qty": -1}], "negative"),
        ]
        for lines, message in cases:
            with self.assertRaisesRegex(UserError, message):
                self.stock_out("breakage", lines)

    # ------------------------------------------------------------------
    # Deliveries
    # ------------------------------------------------------------------

    def test_confirm_a_delivery(self):
        delivery = self.send_to_be([{"product_id": self.jameson.id, "uom_id": self.uom_750.id, "qty": 2}])
        self.assertEqual(delivery.state, "done")
        self.assertEqual(delivery.picking_type_id, self.type_iss_be)
        self.assertEqual(delivery.bar_ack_state, "not_checked")
        # Stock is at the bar whether or not anyone checks.
        self.assertEqual(self.qty(self.jameson, self.loc_be), 150)
        desk = self.desk(self.device_be)
        detail = desk.desk_delivery(self.bar_be.id, self.be_token, delivery.id)
        self.assertEqual(detail["lines"][0]["qty"], 2)
        self.assertEqual(detail["lines"][0]["uom_id"], self.uom_750.id)
        desk.desk_delivery_check(self.bar_be.id, self.be_token, self.uuid(), delivery.id)
        self.assertEqual(delivery.bar_ack_state, "confirmed")
        self.assertEqual(delivery.bar_ack_employee_id, self.mary)
        self.assertTrue(delivery.bar_ack_date)
        with self.assertRaisesRegex(UserError, "already checked"):
            desk.desk_delivery_check(self.bar_be.id, self.be_token, self.uuid(), delivery.id)

    def test_dispute_a_shortage_then_accept_or_reject(self):
        desk = self.desk(self.device_be)
        store_desk = self.desk(self.device_store)
        first = self.send_to_be(
            [
                {"product_id": self.jameson.id, "uom_id": self.uom_750.id, "qty": 2},
                {"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 24},
            ]
        )
        jameson_move = first.move_ids.filtered(lambda move: move.product_id == self.jameson)
        desk.desk_delivery_check(self.bar_be.id, self.be_token, self.uuid(), first.id, {jameson_move.id: 1})
        self.assertEqual(first.bar_ack_state, "disputed")
        dispute = first.bar_dispute_ids
        self.assertEqual(dispute.state, "draft")
        self.assertEqual(dispute.picking_type_id, self.type_ret_be)
        self.assertEqual((dispute.location_id, dispute.location_dest_id), (self.loc_be, self.loc_store))
        self.assertEqual(dispute.move_ids.product_uom_qty, 1)
        self.assertEqual(dispute.move_ids.product_uom, self.uom_750)
        self.assertEqual(dispute.bar_dispute_state, "pending")
        # Nothing moves until the store decides.
        self.assertEqual(self.qty(self.jameson, self.loc_be), 150)

        disputes = store_desk.desk_disputes(self.store.id, self.store_token)
        self.assertEqual([item["id"] for item in disputes], dispute.ids)
        self.assertTrue(disputes[0]["short"])
        self.assertEqual(disputes[0]["bar"], "Bulls Eye")
        store_desk.desk_dispute_resolve(self.store.id, self.store_token, self.uuid(), dispute.id, True)
        self.assertEqual(dispute.state, "done")
        self.assertEqual(dispute.bar_dispute_state, "accepted")
        self.assertEqual(self.qty(self.jameson, self.loc_be), 125)
        self.assertEqual(first.bar_ack_state, "disputed")

        second = self.send_to_be([{"product_id": self.jameson.id, "uom_id": self.uom_tot.id, "qty": 10}])
        desk.desk_delivery_check(self.bar_be.id, self.be_token, self.uuid(), second.id, {second.move_ids.id: 7})
        store_desk.desk_dispute_resolve(
            self.store.id, self.store_token, self.uuid(), second.bar_dispute_ids.id, False
        )
        self.assertEqual(second.bar_dispute_ids.state, "cancel")
        self.assertEqual(second.bar_dispute_ids.bar_dispute_state, "rejected")
        self.assertEqual(self.qty(self.jameson, self.loc_be), 135)  # the variance stays with the bar

    def test_dispute_a_surplus(self):
        delivery = self.send_to_be([{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 24}])
        self.desk(self.device_be).desk_delivery_check(
            self.bar_be.id, self.be_token, self.uuid(), delivery.id, {delivery.move_ids.id: 25}
        )
        extra = delivery.bar_dispute_ids
        self.assertEqual(extra.picking_type_id, self.type_iss_be)
        self.assertEqual((extra.location_id, extra.location_dest_id), (self.loc_store, self.loc_be))
        self.assertEqual(extra.move_ids.product_uom_qty, 1)
        disputes = self.desk(self.device_store).desk_disputes(self.store.id, self.store_token)
        self.assertFalse(disputes[0]["short"])

    def test_dispute_on_a_transfer_goes_back_to_the_sending_bar(self):
        result = self.stock_out(
            "transfer",
            [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 12}],
            dest_bar_id=self.bar_bb.id,
        )
        transfer = self.env["odin.bar.activity"].browse(result["activity_id"]).picking_ids
        self.desk(self.device_bb).desk_delivery_check(
            self.bar_bb.id, self.bb_token, self.uuid(), transfer.id, {transfer.move_ids.id: 10}
        )
        dispute = transfer.bar_dispute_ids
        self.assertEqual(dispute.picking_type_id, self.type_ibt)
        self.assertEqual((dispute.location_id, dispute.location_dest_id), (self.loc_bb, self.loc_be))
        self.assertEqual(dispute.move_ids.product_uom_qty, 2)

    def test_a_bar_only_checks_its_own_deliveries(self):
        delivery = self.send_to_be([{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 6}])
        with self.assertRaisesRegex(UserError, "not for Banda Bar"):
            self.desk(self.device_bb).desk_delivery(self.bar_bb.id, self.bb_token, delivery.id)

    # ------------------------------------------------------------------
    # Store mode
    # ------------------------------------------------------------------

    def test_receive_from_a_supplier(self):
        desk = self.desk(self.device_store)
        self.assertIn(self.supplier.id, [p["id"] for p in desk.desk_suppliers(self.store.id, self.store_token, "EABL")])
        result = desk.desk_store_receive(
            self.store.id,
            self.store_token,
            self.uuid(),
            self.supplier.id,
            [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": 5}],
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

    def test_store_actions_are_store_only(self):
        lines = [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 1}]
        with self.assertRaisesRegex(UserError, "store"):
            self.desk(self.device_be).desk_store_send(self.bar_be.id, self.be_token, self.uuid(), self.bar_bb.id, lines)
        with self.assertRaisesRegex(UserError, "bar"):
            self.desk(self.device_store).desk_stock_out(
                self.store.id, self.store_token, self.uuid(), self.reasons["roma"].id, lines
            )

    # ------------------------------------------------------------------
    # Home and catalogue
    # ------------------------------------------------------------------

    def test_home_shows_the_day(self):
        with freeze_time("2026-09-27 17:00:00"):  # 20:00 at the bar
            self.stock_out("roma", [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 2}])
            self.send_to_be([{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": 1}])
            token = self.login(self.device_be, self.bar_be, self.mary, "1234")
            home = self.desk(self.device_be).desk_home(self.bar_be.id, token)
        self.assertEqual(home["bar"]["day_label"], "Sun 27 Sep")
        self.assertEqual(home["unchecked"], 1)
        titles = [item["title"] for item in home["timeline"]]
        self.assertIn("Roma", titles)
        self.assertIn("Stock in from Main Store", titles)
        roma = next(item for item in home["timeline"] if item["title"] == "Roma")
        self.assertEqual(roma["who"], "Mary")
        self.assertEqual(roma["time"], "20:00")
        self.assertEqual(home["count"]["state"], "none")

    def test_catalog(self):
        catalog = self.desk(self.device_be).desk_catalog(self.bar_be.id, self.be_token)
        products = {product["id"]: product for product in catalog["products"]}
        self.assertNotIn(self.not_on_desk.id, products)
        jameson = products[self.jameson.id]
        self.assertTrue(jameson["poured"])
        self.assertEqual([uom["id"] for uom in jameson["bottles"]], [self.uom_750.id, self.uom_1l.id])
        self.assertEqual([uom["factor"] for uom in jameson["bottles"]], [25.0, 33.0])
        tusker = products[self.tusker.id]
        self.assertFalse(tusker["poured"])
        self.assertEqual([uom["id"] for uom in tusker["packs"]], [self.uom_crate.id])
        self.assertEqual(
            {reason["code"] for reason in catalog["reasons"]},
            {"roma", "event", "debt", "breakage", "spoiled", "flat", "expired", "store", "transfer"},
        )
        self.assertFalse([reason for reason in catalog["reasons"] if reason["setup_issue"]])
        self.assertEqual([bar["id"] for bar in catalog["bars"]], self.bar_bb.ids)

    def test_count_sheet_follows_the_bar_setting(self):
        self.bar_bb.sheet_product_ids = [Command.set([self.tusker.id, self.jameson.id])]
        self.tusker.categ_id.bar_count_sequence = 1
        count = self.desk(self.device_bb).desk_count_start(self.bar_bb.id, self.bb_token)
        self.assertEqual({line["product_id"] for line in count["lines"]}, {self.tusker.id, self.jameson.id})

    def test_defaults_map_to_the_existing_setup(self):
        self.assertEqual(self.bar_be.issue_type_id, self.type_iss_be)
        self.assertEqual(self.bar_be.return_type_id, self.type_ret_be)
        self.assertEqual(self.bar_be.sale_type_id, self.type_sal_be)
        self.assertEqual(self.bar_be.store_id, self.store)
        self.assertEqual(self.store.receipt_type_id, self.warehouse.in_type_id)
        self.assertEqual(self.reasons["event"].picking_type_id, self.type_evt)
        self.assertEqual(self.reasons["transfer"].picking_type_id, self.type_ibt)
        self.assertFalse(self.bar_be.setup_issues)
        self.assertEqual(self.jameson.bar_bottle_uom_id, self.uom_750)
        self.assertEqual(self.jameson.tots_per_bottle, 25)
        self.assertFalse(self.tusker.bar_bottle_uom_id)
