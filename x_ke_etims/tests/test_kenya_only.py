# -*- coding: utf-8 -*-
"""The module must not exist for anyone outside Kenya."""

from unittest.mock import patch

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..backends.demo import DemoAdapter
from .common import KeEtimsCase


@tagged("post_install", "-at_install")
class TestKenyaOnly(KeEtimsCase):

    def us_invoice(self, post=True):
        product = self.env["product.product"].create({
            "name": "Hammer", "list_price": 25.0,
            "taxes_id": [Command.set(self.us_data["default_tax_sale"].ids)],
        })
        return self.init_invoice("out_invoice", partner=self.partner_a, products=product,
                                 company=self.us_company, post=post)

    def test_a_us_invoice_posts_resets_and_prints_as_before(self):
        with patch.object(DemoAdapter, "_sign", side_effect=AssertionError("called")):
            invoice = self.us_invoice()
        self.assertEqual(invoice.state, "posted")
        self.assertFalse(invoice.ke_etims_document_ids)
        self.assertFalse(invoice.ke_etims_state)
        self.assertFalse(invoice.ke_etims_applicable)
        self.assertFalse([key for key in invoice._get_alerts() if key.startswith("ke_etims")])
        self.assertTrue(invoice.show_reset_to_draft_button)
        invoice.button_draft()
        self.assertEqual(invoice.state, "draft")
        invoice.action_post()
        html = self.env["ir.actions.report"]._render_qweb_html(
            "account.report_invoice", invoice.ids)[0].decode()
        self.assertNotIn("SCU INFORMATION", html)
        self.assertNotIn("eTIMS", html)
        self.assertNotIn("TAX INVOICE", html)

    def test_the_queue_never_touches_a_us_company(self):
        invoice = self.us_invoice()
        self.run_queue()
        self.assertFalse(self.env["ke.etims.document"].search(
            [("company_id", "=", self.us_company.id)]))
        self.assertFalse(invoice.ke_etims_document_ids)

    def test_a_device_cannot_be_made_for_a_us_company(self):
        with self.assertRaises(ValidationError) as caught:
            self.make_device(self.us_company)
        self.assertIn("Kenyan companies only", str(caught.exception))

    def test_a_kenyan_company_without_a_device_is_untouched(self):
        self.device.active = False
        invoice = self.ke_invoice()
        self.assertFalse(invoice.ke_etims_document_ids)
        self.assertFalse(invoice.ke_etims_applicable)
        self.assertFalse(invoice.ke_etims_state)

    def test_tremol_stands_aside_only_where_etims_is_live(self):
        self.assertTrue(self.ke_company.l10n_ke_oscu_is_active)
        self.assertFalse(self.us_company.l10n_ke_oscu_is_active)

    def test_the_list_column_is_only_there_for_someone_in_kenya(self):
        view = self.env.ref("account.view_invoice_tree")
        Move = self.env["account.move"]
        us_only = Move.with_context(allowed_company_ids=self.us_company.ids)
        arch = us_only.get_view(view.id, "list")["arch"]
        self.assertNotIn("ke_etims_state", arch)
        search = us_only.get_view(self.env.ref("account.view_account_invoice_filter").id,
                                  "search")["arch"]
        self.assertNotIn("ke_etims", search)
        with_kenya = Move.with_context(
            allowed_company_ids=(self.ke_company | self.us_company).ids)
        self.assertIn("ke_etims_state", with_kenya.get_view(view.id, "list")["arch"])

    def test_settings_and_partner_fields_follow_the_current_company(self):
        partner = self.partner_a
        self.assertTrue(partner.with_company(self.ke_company).ke_etims_show)
        self.assertFalse(partner.with_company(self.us_company).ke_etims_show)
        template = self.product.product_tmpl_id
        self.assertTrue(template.with_company(self.ke_company).ke_etims_show)
        self.assertFalse(template.with_company(self.us_company).ke_etims_show)
