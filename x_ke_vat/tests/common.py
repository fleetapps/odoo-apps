# -*- coding: utf-8 -*-
"""Shared setup for the Kenyan VAT tests.

Everything here builds on Odoo's own ``AccountTestInvoicingCommon`` with the
Kenyan chart of accounts loaded, because the whole point of this module is that
it reads the taxes and tags ``l10n_ke`` installs. A test that invented its own
taxes would pass while the module was broken.

Taxes are looked up the way the rest of the module looks them up: by the
per-company external id that ``account.chart.template`` gives every generated
record. If that convention ever changes, these tests fail for the same reason
the module would, which is the point.
"""

from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class KeVatCase(AccountTestInvoicingCommon):

    @classmethod
    @AccountTestInvoicingCommon.setup_country("ke")
    def setUpClass(cls):
        super().setUpClass()

        cls.ke_company = cls.company_data["company"]
        cls.render = cls.env["ke.vat.render"]
        cls.vat3 = cls.env.ref("l10n_ke.tax_report_ke")

        # AccountTestInvoicingCommon grants account.group_account_manager but
        # not base.group_system, which is what carries our manager group on a
        # real database. Grant it here so closing a period is testable without
        # every test having to remember to.
        cls.env.user.sudo().group_ids = [
            (4, cls.env.ref("x_ke_base.group_ke_tax_manager").id)]

        cls.customer = cls.env["res.partner"].create({
            "name": "Nairobi Trading Ltd",
            "vat": "P051234567X",
            "country_id": cls.env.ref("base.ke").id,
        })
        cls.supplier = cls.env["res.partner"].create({
            "name": "Mombasa Supplies Ltd",
            "vat": "P059876543Y",
            "country_id": cls.env.ref("base.ke").id,
        })

        # A whole calendar month, which is what a Kenyan VAT period is.
        cls.period_from = fields.Date.to_date("2026-08-01")
        cls.period_to = fields.Date.to_date("2026-08-31")

    # ------------------------------------------------------------ fixtures

    @classmethod
    def ke_tax(cls, template_id, company=None):
        """A Kenyan tax by its l10n_ke template id, e.g. 'ST16', 'ST0EXPORT'."""
        company = company or cls.ke_company
        xmlid = cls.env["account.chart.template"].company_xmlid(
            template_id, company)
        tax = cls.env.ref(xmlid, raise_if_not_found=False)
        assert tax, "l10n_ke did not install the tax %s" % template_id
        return tax

    @classmethod
    def ke_tag(cls, name):
        return cls.ke_company._ke_tag(name)

    @classmethod
    def post_invoice(cls, template_id, amount, move_type="out_invoice",
                     date=None, partner=None, ref=None):
        """Post one single-line invoice carrying one Kenyan tax."""
        tax = cls.ke_tax(template_id)
        date = date or cls.period_from
        partner = partner or (
            cls.customer if move_type.startswith("out") else cls.supplier)
        move = cls.env["account.move"].create({
            "move_type": move_type,
            "partner_id": partner.id,
            "invoice_date": date,
            "date": date,
            "ref": ref or False,
            "company_id": cls.ke_company.id,
            "invoice_line_ids": [(0, 0, {
                "name": "Line for %s" % template_id,
                "quantity": 1,
                "price_unit": amount,
                "tax_ids": [(6, 0, tax.ids)],
            })],
        })
        move.action_post()
        return move

    # -------------------------------------------------------------- render

    @classmethod
    def options(cls, date_from=None, date_to=None, state="posted"):
        return {
            "date_from": date_from or cls.period_from,
            "date_to": date_to or cls.period_to,
            "company_ids": cls.ke_company.ids,
            "state": state,
            "journal_ids": [],
        }

    def box(self, rendered, code, label="tax"):
        """The value of one VAT3 box, e.g. box('box_1', 'base')."""
        for line in rendered["lines"]:
            if line["code"] == code:
                return line["values"].get(label)
        raise AssertionError("No box %s in the rendered return" % code)

    def make_return(self, date_from=None, date_to=None):
        return self.env["ke.vat.return"].create({
            "company_id": self.ke_company.id,
            "report_id": self.vat3.id,
            "date_from": date_from or self.period_from,
            "date_to": date_to or self.period_to,
        })
