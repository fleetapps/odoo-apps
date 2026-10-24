# -*- coding: utf-8 -*-
"""Shared setup: a Kenyan company with eTIMS beside an American one without.

Every test runs in a database holding both, because the first promise of this
module is that the second one does not notice it is installed. The Kenyan
company runs the real ``l10n_ke`` chart, and its taxes are looked up by their
per-company external ids, the way x_ke_base and the module itself find them.
"""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..backends.demo import DemoAdapter


class KeEtimsCase(AccountTestInvoicingCommon):

    @classmethod
    @AccountTestInvoicingCommon.setup_country("ke")
    def setUpClass(cls):
        super().setUpClass()
        cls.ke_company = cls.company_data["company"]
        cls.ke_company.write({"vat": "P051234567X", "name": "Nairobi Traders Ltd"})
        cls.env.user.sudo().group_ids = [
            Command.link(cls.env.ref("x_ke_etims.group_ke_etims_manager").id)]

        # The generic chart, so the test needs no other localisation installed.
        cls.chart_template = "generic_coa"
        try:
            cls.us_data = cls.setup_other_company(
                name="Austin Hardware Inc", country_id=cls.env.ref("base.us").id,
                currency_id=cls.env.ref("base.USD").id)
        finally:
            cls.chart_template = False
        cls.us_company = cls.us_data["company"]
        cls.us_company.account_fiscal_country_id = cls.env.ref("base.us")

        cls.device = cls.make_device(cls.ke_company)
        cls.device.action_initialize()

        cls.salesperson = cls.env["res.users"].create({
            "name": "Wanjiku Accountant",
            "login": "wanjiku",
            "email": "wanjiku@example.com",
            "company_id": cls.ke_company.id,
            "company_ids": [Command.set(cls.ke_company.ids)],
            "group_ids": [Command.set([cls.env.ref("account.group_account_invoice").id,
                                       cls.env.ref("base.group_user").id])],
        })
        cls.customer = cls.env["res.partner"].create({
            "name": "Mombasa Retail Ltd", "vat": "P059876543Y",
            "country_id": cls.env.ref("base.ke").id,
        })
        cls.item_class = cls.env["ke.etims.item.class"].search(
            [("code", "=", "5059690800")], limit=1)
        cls.st16 = cls.ke_tax("ST16")
        cls.product = cls.make_product("Packaged maize flour", 1160.0, cls.st16)

    # ------------------------------------------------------------ fixtures

    @classmethod
    def make_device(cls, company, backend="demo", environment="demo", **values):
        return cls.env["ke.etims.device"].create({
            "company_id": company.id,
            "backend": backend,
            "environment": environment,
            "tin": values.pop("tin", "P051234567X"),
            "bhf_id": values.pop("bhf_id", company.ke_etims_bhf_id or "00"),
            **values,
        })

    @classmethod
    def make_branch(cls, name, bhf_id):
        branch = cls.env["res.company"].create({
            "name": name, "parent_id": cls.ke_company.id,
            "country_id": cls.env.ref("base.ke").id, "ke_etims_bhf_id": bhf_id,
        })
        cls.env.cr.precommit.run()  # a branch's share of the chart loads on commit
        return branch

    @classmethod
    def ke_tax(cls, template_id, company=None):
        company = company or cls.ke_company
        xmlid = cls.env["account.chart.template"].company_xmlid(template_id, company)
        tax = cls.env.ref(xmlid, raise_if_not_found=False)
        assert tax, "l10n_ke did not install the tax %s" % template_id
        return tax

    @classmethod
    def make_product(cls, name, price, tax, item_class=True):
        return cls.env["product.product"].create({
            "name": name,
            "type": "consu",
            "list_price": price,
            "taxes_id": [Command.set(tax.ids)],
            "ke_etims_item_class_id": cls.item_class.id if item_class else False,
            "property_account_income_id": cls.company_data["default_account_revenue"].id,
        })

    def ke_invoice(self, products=None, move_type="out_invoice", post=True, partner=None,
                   prices=None, company=None):
        products = products or self.product
        company = company or self.ke_company
        move = self.env["account.move"].with_company(company).create({
            "move_type": move_type,
            "partner_id": (partner or self.customer).id,
            "invoice_date": fields.Date.today(),
            "invoice_user_id": self.salesperson.id,
            "company_id": company.id,
            "invoice_line_ids": [
                Command.create({
                    "product_id": product.id,
                    "quantity": 1,
                    "price_unit": (prices or {}).get(product, product.list_price),
                    "tax_ids": [Command.set(product.taxes_id.filtered(
                        lambda t: t.company_id in company.parent_ids).ids)],
                })
                for product in products
            ],
        })
        if post:
            move.action_post()
        return move

    def run_queue(self, devices=None):
        """Run the queue as the cron would, with every retry due now."""
        Document = self.env["ke.etims.document"].sudo()
        Document.search([("state", "in", ("to_send", "pending"))]).write(
            {"next_try": fields.Datetime.now() - timedelta(seconds=1)})
        Document._ke_etims_process_queue(devices=devices)

    def credit(self, invoice, reason="06"):
        reason_code = self.env["ke.etims.code"]._ke_etims_find("32", reason)
        wizard = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=invoice.ids).create({
                "journal_id": invoice.journal_id.id,
                "reason": "Returned",
                "ke_etims_reason_code_id": reason_code.id,
            })
        action = wizard.refund_moves()
        refund = self.env["account.move"].browse(action["res_id"])
        refund.action_post()
        return refund

    @contextmanager
    def demo_fails(self, error):
        """Make the demo backend fail every signing with ``error``."""
        def fail(adapter, sale):
            raise error
        with patch.object(DemoAdapter, "_sign", fail):
            yield
