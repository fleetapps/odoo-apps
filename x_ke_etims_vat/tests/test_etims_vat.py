# -*- coding: utf-8 -*-
"""eTIMS numbers in the iTax reconciliation, and bills without an eTIMS invoice."""

import base64

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.x_ke_etims.tests.common import KeEtimsCase

HEADER = "Invoice No,Invoice Date,PIN of Purchaser,Taxable Value,Amount of VAT"


@tagged("post_install_l10n", "post_install", "-at_install")
class TestEtimsVat(KeEtimsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.sudo().group_ids = [
            Command.link(cls.env.ref("x_ke_base.group_ke_tax_manager").id)]
        cls.today = fields.Date.context_today(cls.env.user)
        cls.supplier = cls.env["res.partner"].create({
            "name": "Kisumu Packaging Ltd", "vat": "P051111111A",
            "country_id": cls.env.ref("base.ke").id,
        })
        cls.overseas = cls.env["res.partner"].create({
            "name": "Shenzhen Components Co", "country_id": cls.env.ref("base.cn").id,
        })

    def setUp(self):
        super().setUp()
        self.vat_return = self.env["ke.vat.return"].create({
            "company_id": self.ke_company.id,
            "report_id": self.env.ref("l10n_ke.tax_report_ke").id,
            "date_from": self.today + relativedelta(day=1),
            "date_to": self.today + relativedelta(day=31),
        })

    # ------------------------------------------------------------ fixtures

    def signed_invoice(self):
        move = self.ke_invoice()
        self.run_queue()
        self.assertEqual(move.ke_etims_state, "sent")
        return move

    def bill(self, partner=None, ref="SUP-1", etims_ref=False, date=None, company=None,
             amount=5000.0, **values):
        company = company or self.ke_company
        move = self.env["account.move"].with_company(company).create({
            "move_type": "in_invoice",
            "partner_id": (partner or self.supplier).id,
            "company_id": company.id,
            "invoice_date": date or self.today,
            "ref": ref,
            "ke_etims_supplier_invoice_ref": etims_ref,
            "invoice_line_ids": [Command.create({
                "name": "Cartons", "quantity": 1, "price_unit": amount, "tax_ids": []})],
            **values,
        })
        move.action_post()
        return move

    def batch(self, *rows, section="sales_pin"):
        batch = self.env["ke.vat.itax.batch"].create({
            "return_id": self.vat_return.id,
            "section": section,
            "csv_file": base64.b64encode("\n".join((HEADER,) + rows).encode()),
            "csv_filename": "%s.csv" % section,
        })
        batch.action_import()
        return batch

    def row(self, number, move, pin=None, taxable=None, tax=None):
        return "%s,%s,%s,%s,%s" % (
            number, self.today.strftime("%d/%m/%Y"), pin or move.commercial_partner_id.vat,
            move.amount_untaxed if taxable is None else taxable,
            move.amount_tax if tax is None else tax)

    # ------------------------------------------------------ iTax matching

    def test_a_row_under_the_cu_invoice_number_is_matched(self):
        move = self.signed_invoice()
        self.assertTrue(move.ke_etims_cu_invoice)
        batch = self.batch(self.row(move.ke_etims_cu_invoice, move))

        self.assertEqual(batch.line_ids.match_state, "matched")
        self.assertEqual(batch.line_ids.move_id, move)
        self.assertEqual(batch.line_ids.ke_etims_number, move.ke_etims_cu_invoice)
        self.assertEqual(batch.line_ids.ke_etims_state, "sent")

    def test_a_row_under_the_trader_invoice_number_is_matched(self):
        move = self.signed_invoice()
        number = move.ke_etims_document_id.kra_invoice_no
        batch = self.batch(self.row(number, move))

        self.assertEqual(batch.line_ids.match_state, "matched")
        self.assertEqual(batch.line_ids.move_id, move)

    def test_an_etims_match_still_compares_amounts(self):
        move = self.signed_invoice()
        batch = self.batch(self.row(move.ke_etims_cu_invoice, move,
                                    taxable=move.amount_untaxed - 100))

        self.assertEqual(batch.line_ids.match_state, "amount_variance")
        self.assertEqual(batch.line_ids.move_id, move)

    def test_the_odoo_number_still_matches_first(self):
        move = self.signed_invoice()
        batch = self.batch(self.row(move.name, move))
        self.assertEqual(batch.line_ids.match_state, "matched")

    def test_one_document_is_not_matched_by_two_rows(self):
        move = self.signed_invoice()
        batch = self.batch(self.row(move.name, move),
                           self.row(move.ke_etims_cu_invoice, move))

        by_number = {line.kra_invoice_no: line for line in batch.line_ids}
        self.assertEqual(by_number[move.name.upper()].match_state, "matched")
        self.assertEqual(by_number[move.ke_etims_cu_invoice.upper()].match_state, "kra_only")

    def test_an_unsigned_invoice_is_still_reported_missing(self):
        unsigned = self.ke_invoice()
        self.assertEqual(unsigned.ke_etims_state, "to_send")
        batch = self.batch("KRACU0100009999/1,%s,P059876543Y,1000,160"
                           % self.today.strftime("%d/%m/%Y"))

        states = {line.match_state: line for line in batch.line_ids}
        self.assertIn("kra_only", states)
        self.assertEqual(states["odoo_only"].move_id, unsigned)

    def test_a_purchase_row_under_the_supplier_cu_number_is_matched(self):
        bill = self.bill(etims_ref="KRACU0100000001/152")
        batch = self.batch(self.row("KRACU0100000001/152", bill), section="purchases_local")

        self.assertEqual(batch.line_ids.match_state, "matched")
        self.assertEqual(batch.line_ids.move_id, bill)
        self.assertEqual(batch.line_ids.ke_etims_number, "KRACU0100000001/152")

    def test_a_purchase_row_prefers_the_supplier_with_that_pin(self):
        other = self.env["res.partner"].create({
            "name": "Eldoret Mills", "vat": "P052222222B",
            "country_id": self.env.ref("base.ke").id})
        # Two suppliers' devices can both have issued receipt 152.
        self.bill(partner=other, ref="E-1", etims_ref="KRACU0100000001/152")
        ours = self.bill(ref="K-1", etims_ref="KRACU0100000001/152")
        batch = self.batch(self.row("KRACU0100000001/152", ours), section="purchases_local")

        kra_row = batch.line_ids.filtered("kra_invoice_no")
        self.assertEqual(kra_row.match_state, "matched")
        self.assertEqual(kra_row.move_id, ours)

    # ------------------------------------------------- bills without eTIMS

    def missing(self):
        return self.env["account.move"].search(
            [("ke_etims_invoice_missing", "=", True), ("state", "=", "posted")])

    def test_a_local_bill_without_an_etims_number_is_flagged(self):
        bill = self.bill()
        self.assertTrue(bill.ke_etims_invoice_missing)
        self.assertIn(bill, self.missing())
        self.assertIn("ke_etims_supplier_invoice_missing", bill.alerts)

        bill.ke_etims_supplier_invoice_ref = "KRACU0100000001/152"
        self.assertFalse(bill.ke_etims_invoice_missing)
        self.assertNotIn(bill, self.missing())
        self.assertNotIn("ke_etims_supplier_invoice_missing", bill.alerts or {})

    def test_bills_the_rule_does_not_cover_are_not_flagged(self):
        bank = self.env["res.partner"].create({
            "name": "Equity Bank", "country_id": self.env.ref("base.ke").id,
            "ke_etims_no_invoice_needed": True})
        before = self.ke_company.ke_etims_deductible_from - relativedelta(days=1)
        exempt = (
            self.bill(partner=self.overseas)
            | self.bill(partner=bank)
            | self.bill(ke_etims_not_required=True)
            | self.bill(date=before)
            | self.bill(ke_etims_reverse_invoice=True)
        )
        flagged = self.bill()
        self.assertFalse(any(exempt.mapped("ke_etims_invoice_missing")))
        self.assertEqual(self.missing() & (exempt | flagged), flagged)
        self.assertEqual(self.env["account.move"].search(
            [("ke_etims_invoice_missing", "=", False), ("id", "in", (exempt | flagged).ids)]),
            exempt)

    def test_a_company_can_turn_the_check_off(self):
        bill = self.bill()
        self.ke_company.ke_etims_deductible_from = False
        self.assertFalse(bill.ke_etims_invoice_missing)
        self.assertNotIn(bill, self.missing())

    def test_customer_invoices_and_other_countries_are_not_flagged(self):
        us_bill = self.env["account.move"].with_company(self.us_company).create({
            "move_type": "in_invoice",
            "partner_id": self.supplier.id,
            "company_id": self.us_company.id,
            "invoice_date": self.today,
            "invoice_line_ids": [Command.create({"name": "Bolts", "price_unit": 10.0,
                                                 "tax_ids": []})],
        })
        us_bill.action_post()
        invoice = self.ke_invoice()
        self.assertFalse(us_bill.ke_etims_invoice_missing)
        self.assertFalse(invoice.ke_etims_invoice_missing)
        self.assertFalse(self.missing() & (us_bill | invoice))

    # ------------------------------------------------------------ views

    def test_the_tax_audit_list_has_the_column_for_kenya_only(self):
        view = self.env.ref("account.view_move_line_tax_audit_tree")
        Line = self.env["account.move.line"]
        ke_arch = Line.with_context(allowed_company_ids=self.ke_company.ids).get_view(
            view.id, "list")["arch"]
        us_arch = Line.with_context(allowed_company_ids=self.us_company.ids).get_view(
            view.id, "list")["arch"]
        self.assertIn('name="ke_etims_number"', ke_arch)
        self.assertNotIn("ke_etims_number", us_arch)

    def test_the_bill_list_action_opens(self):
        bill = self.bill()
        action = self.env.ref("x_ke_etims_vat.action_ke_etims_bills_missing")
        self.assertIn(bill, self.env["account.move"].search(safe_eval(action.domain)))
