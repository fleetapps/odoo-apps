from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import freeze_time, new_test_user, tagged

from odoo.addons.odin_bar_desk.models.odin_bar_desk import DeskSessionError

from .common import BarDeskCase


@tagged("post_install", "-at_install")
class TestDeskAccess(BarDeskCase):
    def test_staff_login_cannot_act_for_another_bar(self):
        token = self.login(self.device_be, self.bar_be, self.mary, "1234")
        desk = self.desk(self.device_be)
        lines = [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 1}]
        calls = [
            lambda: desk.desk_open_bar(self.bar_bb.id),
            lambda: desk.desk_login(self.bar_bb.id, self.john.id, "5678"),
            lambda: desk.desk_home(self.bar_bb.id, token),
            lambda: desk.desk_catalog(self.bar_bb.id, token),
            lambda: desk.desk_stock_out(self.bar_bb.id, token, self.uuid(), self.reasons["breakage"].id, lines),
            lambda: desk.desk_count_start(self.bar_bb.id, token),
            lambda: desk.desk_deliveries(self.bar_bb.id, token),
            lambda: desk.desk_store_send(self.store.id, token, self.uuid(), self.bar_bb.id, lines),
        ]
        for call in calls:
            with self.assertRaises(AccessError):
                call()
        self.assertFalse(self.env["odin.bar.activity"].search([("bar_id", "=", self.bar_bb.id)]))
        self.assertFalse(self.env["stock.scrap"].search([("location_id", "=", self.loc_bb.id)]))

    def test_desk_list_only_offers_the_device_bar(self):
        boot = self.desk(self.device_be).desk_boot()
        self.assertEqual([bar["id"] for bar in boot["bars"]], self.bar_be.ids)
        self.assertFalse(boot["is_manager"])
        manager_boot = self.desk(self.manager).desk_boot()
        self.assertEqual(
            {bar["id"] for bar in manager_boot["bars"]}, set((self.store | self.bar_be | self.bar_bb).ids)
        )

    def test_token_is_tied_to_login_bar_staff_and_time(self):
        with freeze_time("2026-09-27 10:00:00"):
            token = self.login(self.device_be, self.bar_be, self.mary, "1234")
            desk = self.desk(self.device_be)
            desk.desk_home(self.bar_be.id, token)
            for bad in ("", "garbage", token[:-1] + ("0" if token[-1] != "0" else "1")):
                with self.assertRaises(DeskSessionError):
                    desk.desk_home(self.bar_be.id, bad)
            # A manager can reach the bar, but not with a token signed for another login.
            with self.assertRaises(DeskSessionError):
                self.desk(self.manager).desk_home(self.bar_be.id, token)
        with freeze_time("2026-09-28 03:00:00"):  # 17 hours later
            with self.assertRaises(DeskSessionError):
                desk.desk_home(self.bar_be.id, token)
        with freeze_time("2026-09-27 11:00:00"):
            self.mary.odin_bar_ids = [Command.clear()]
            with self.assertRaises(DeskSessionError):
                desk.desk_home(self.bar_be.id, token)

    def test_staff_cannot_see_stock(self):
        """Counts are blind: a staff login cannot read stock levels or history,
        not even the quantities every internal user normally sees."""
        self.opening_stock(self.loc_be, [(self.jameson, 50)])
        for model in ("stock.picking", "stock.move", "odin.bar.count", "odin.bar.count.line", "odin.bar.activity"):
            with self.assertRaises(AccessError):
                self.env[model].with_user(self.device_be).search([], limit=1)
        for model in ("stock.quant", "stock.move.line", "report.stock.quantity"):
            self.assertFalse(self.env[model].with_user(self.device_be).search([]))
        with self.assertRaises(AccessError):
            self.jameson.with_user(self.device_be).with_context(location=self.loc_be.id).read(["qty_available"])
        # Managers still see stock.
        self.assertEqual(self.jameson.with_user(self.manager).with_context(location=self.loc_be.id).qty_available, 50)
        token = self.login(self.device_be, self.bar_be, self.mary, "1234")
        self.desk(self.device_be).desk_home(self.bar_be.id, token)

    def test_logins_without_the_desk_group_are_refused(self):
        clerk = new_test_user(self.env, login="clerk", groups="base.group_user", odin_bar_id=self.bar_be.id)
        with self.assertRaises(AccessError):
            self.desk(clerk).desk_boot()
        with self.assertRaises(AccessError):
            self.desk(clerk).desk_open_bar(self.bar_be.id)

    def test_pin(self):
        desk = self.desk(self.device_be)
        self.assertEqual(desk.desk_login(self.bar_be.id, self.mary.id, "0000"), {"error": "Wrong PIN."})
        self.assertEqual(
            self.env["odin.bar.activity"].search_count(
                [("kind", "=", "pin_fail"), ("employee_id", "=", self.mary.id)]
            ),
            1,
        )
        for _attempt in range(4):
            desk.desk_login(self.bar_be.id, self.mary.id, "9999")
        # Locked out for a while, even with the right PIN.
        self.assertIn("Too many wrong PINs", desk.desk_login(self.bar_be.id, self.mary.id, "1234")["error"])
        # John works at Banda Bar, not here.
        with self.assertRaises(AccessError):
            desk.desk_login(self.bar_be.id, self.john.id, "5678")
        nopin = self.env["hr.employee"].create({"name": "New hire", "odin_bar_ids": [Command.set(self.bar_be.ids)]})
        self.assertIn("no PIN", desk.desk_login(self.bar_be.id, nopin.id, "")["error"])

    def test_staff_who_work_at_every_bar(self):
        relief = self.env["hr.employee"].create({"name": "Relief", "pin": "2468", "odin_bar_all": True})
        for device, bar in ((self.device_be, self.bar_be), (self.device_bb, self.bar_bb)):
            names = [employee["name"] for employee in self.desk(device).desk_open_bar(bar.id)["employees"]]
            self.assertIn("Relief", names)
            token = self.login(device, bar, relief, "2468")
            self.assertEqual(self.desk(device).desk_home(bar.id, token)["employee"]["name"], "Relief")
        # Signing in everywhere does not unlock a bar tablet for another bar.
        with self.assertRaises(AccessError):
            self.desk(self.device_be).desk_login(self.bar_bb.id, relief.id, "2468")

    def test_roving_login_switches_between_its_bars(self):
        roving = new_test_user(
            self.env,
            login="supervisor.phone",
            groups="odin_bar_desk.group_bar_desk_staff",
            odin_bar_id=self.bar_be.id,
            odin_bar_ids=[Command.set(self.bar_bb.ids)],
        )
        boot = self.desk(roving).desk_boot()
        self.assertEqual({bar["id"] for bar in boot["bars"]}, set((self.bar_be | self.bar_bb).ids))
        self.assertEqual(boot["bar_id"], self.bar_be.id)
        self.assertFalse(boot["is_manager"])
        self.mary.odin_bar_ids = [Command.set((self.bar_be | self.bar_bb).ids)]
        for bar in (self.bar_be, self.bar_bb):
            token = self.login(roving, bar, self.mary, "1234")
            self.desk(roving).desk_home(bar.id, token)
        with self.assertRaises(AccessError):
            self.desk(roving).desk_open_bar(self.store.id)

    def test_manager_signs_in_as_themselves_anywhere(self):
        boss = self.env["hr.employee"].create({"name": "Boss", "user_id": self.manager.id})
        desk = self.desk(self.manager)
        bar_screen = desk.desk_open_bar(self.bar_bb.id)
        self.assertIn(boss.id, [employee["id"] for employee in bar_screen["employees"]])
        token = desk.desk_login(self.bar_bb.id, boss.id)["token"]
        self.assertEqual(desk.desk_home(self.bar_bb.id, token)["employee"]["name"], "Boss")

    def test_only_managers_approve(self):
        count = self.submit_count(self.device_be, self.bar_be, self.mary, "1234", [])
        stock_user = new_test_user(self.env, login="stockman", groups="stock.group_stock_user")
        with self.assertRaises(AccessError):
            count.with_user(stock_user).action_approve()
        with self.assertRaises(AccessError):
            count.with_user(self.device_be).action_approve()
