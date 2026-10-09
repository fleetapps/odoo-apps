"""Anomalies: each check fires on what it looks for and stays quiet
otherwise; findings resolve when their condition goes away, never when a
check fails, and a dismissed one stays dismissed."""

from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import freeze_time, tagged

from .common import AiCase, message


@freeze_time("2026-10-09")
@tagged("post_install", "-at_install")
class TestAnomaly(AiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create({"name": "Supplier One"})
        cls.Detector = cls.env["odin.ai.detector"]
        cls.Finding = cls.env["odin.ai.finding"]

    def bill(self, day, amount, partner=None, ref=None):
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": (partner or self.vendor).id, "invoice_date": day, "ref": ref,
            "invoice_line_ids": [Command.create({
                "name": "Service", "quantity": 1, "price_unit": amount, "tax_ids": [Command.clear()],
                "account_id": self.opex.id})],
        })
        bill.action_post()
        return bill

    def run_checks(self):
        return self.Detector.search([])._run(self.env.company)

    def findings(self, code):
        return self.Finding.search([("detector_id.code", "=", code)])

    def test_duplicate_bills_and_their_resolution(self):
        first = self.bill("2026-09-20", 640.0, ref="A-1")
        second = self.bill("2026-09-22", 640.0, ref="A-2")
        self.bill("2026-09-21", 641.0, ref="A-3")
        self.run_checks()
        finding = self.findings("duplicate_bill")
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding.move_ids, first | second)
        self.assertEqual(finding.severity, "high")
        second.button_draft()
        second.button_cancel()
        self.run_checks()
        self.assertEqual(finding.state, "auto_resolved")

    def test_a_dismissed_finding_stays_dismissed(self):
        self.bill("2026-09-20", 640.0)
        self.bill("2026-09-21", 640.0)
        self.run_checks()
        finding = self.findings("duplicate_bill")
        finding.with_user(self.accountant).action_dismiss()
        self.run_checks()
        self.assertEqual(finding.state, "dismissed")
        self.assertEqual(len(self.findings("duplicate_bill")), 1)

    def test_a_failing_check_resolves_nothing(self):
        self.bill("2026-09-20", 640.0)
        self.bill("2026-09-21", 640.0)
        self.run_checks()
        finding = self.findings("duplicate_bill")
        with patch.object(self.registry["odin.ai.detector"], "_detect_duplicate_bill", side_effect=ValueError("boom")), \
                self.assertLogs("odoo.addons.odin_account_ai.models.odin_ai_anomaly", level="ERROR"):
            self.run_checks()
        self.assertEqual(finding.state, "open")
        self.assertIn("boom", self.env.ref("odin_account_ai.detector_duplicate_bill").last_status)

    def test_an_unusual_amount(self):
        for month, amount in zip(range(3, 9), (100.0, 104.0, 98.0, 101.0, 99.0, 103.0)):
            self.bill(f"2026-0{month}-10", amount)
        normal = self.bill("2026-09-25", 102.0)
        unusual = self.bill("2026-09-26", 1000.0)
        self.run_checks()
        findings = self.findings("amount_outlier")
        self.assertEqual(findings.move_ids, unusual)
        self.assertNotIn(normal, findings.move_ids)
        self.assertEqual(findings.severity, "high")

    def test_an_account_new_for_the_supplier(self):
        for month in range(3, 9):
            self.bill(f"2026-0{month}-10", 100.0)
        odd = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.vendor.id, "invoice_date": "2026-09-28",
            "invoice_line_ids": [Command.create({"name": "Odd", "quantity": 1, "price_unit": 50.0,
                                                 "tax_ids": [Command.clear()], "account_id": self.cos.id})],
        })
        odd.action_post()
        self.run_checks()
        self.assertEqual(self.findings("new_account").move_ids, odd)

    def test_a_large_manual_entry_and_a_backdated_one(self):
        self.env.ref("odin_account_ai.detector_large_manual").params = '{"min_amount": 500}'
        cash = self.company_data["default_journal_bank"].default_account_id
        large = self.env["account.move"].create({
            "move_type": "entry", "journal_id": self.misc.id, "date": "2026-09-30",
            "line_ids": [Command.create({"account_id": cash.id, "debit": 900.0, "name": "x"}),
                         Command.create({"account_id": self.counterpart.id, "credit": 900.0, "name": "x"})],
        })
        large.action_post()
        small = self.post("2026-09-29", [(self.opex, 100.0)])
        backdated = self.post("2026-06-01", [(self.opex, 10.0)])
        # create_date is the database's clock, not the frozen one: pin it.
        self.env.cr.execute("UPDATE account_move SET create_date = '2026-10-05 10:00' WHERE id IN %s",
                            [(small.id, backdated.id)])
        (small | backdated).invalidate_recordset(["create_date"])
        self.run_checks()
        self.assertEqual(self.findings("large_manual").move_ids, large)
        self.assertIn(backdated, self.findings("backdated").move_ids)
        self.assertNotIn(small, self.findings("backdated").move_ids)

    def test_bank_lines_left_unreconciled(self):
        line = self.env["account.bank.statement.line"].create({
            "journal_id": self.company_data["default_journal_bank"].id, "date": "2026-08-01",
            "payment_ref": "OLD", "amount": 70.0})
        self.run_checks()
        finding = self.findings("stale_bank")
        self.assertEqual(len(finding), 1)
        self.assertAlmostEqual(finding.amount, 70.0)
        line.unlink()
        self.run_checks()
        self.assertEqual(finding.state, "auto_resolved")

    def test_missing_analytic_only_where_analytics_are_used(self):
        for day in ("2026-07-01", "2026-07-02", "2026-07-03"):
            self.post(day, [(self.opex, 10.0)])
        untagged = self.post("2026-09-20", [(self.opex, 10.0)])
        self.run_checks()
        self.assertFalse(self.findings("missing_analytic"), "this company does not use analytics")
        for day in ("2026-07-04", "2026-07-05", "2026-07-06", "2026-07-07"):
            self.post(day, [(self.opex, 10.0, {"analytic": {str(self.sales_dept.id): 100.0}})])
        self.run_checks()
        self.assertIn(untagged, self.findings("missing_analytic").move_ids)

    def test_explain_with_ai(self):
        self.bill("2026-09-20", 640.0)
        self.bill("2026-09-21", 640.0)
        self.run_checks()
        finding = self.findings("duplicate_bill").with_user(self.accountant)
        requests = self.mock_api(message({
            "summary": "Two bills look the same.", "likely_causes": ["Entered twice."],
            "suggested_action": "Cancel the copy.", "evidence": ["E1", "E2"]}))
        finding.action_explain_ai()
        self.assertEqual(finding.ai_summary, "Two bills look the same.")
        self.assertEqual(finding.ai_action, "Cancel the copy.")
        self.assertIn('"ref": "E2"', requests[0]["messages"][0]["content"])
        self.assertEqual(requests[0]["output_config"]["effort"], "medium")

    def test_rights(self):
        with self.assertRaises(AccessError):
            self.Detector.with_user(self.billing).action_run_all()
        action = self.Detector.with_user(self.accountant).action_run_all()
        self.assertEqual(action["tag"], "display_notification")
