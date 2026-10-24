# -*- coding: utf-8 -*-
"""Each Kenyan tax knows which KRA tax type it transmits as.

The tax type is a row of KRA's code list (class 04), never a letter typed into
a selection. Its default comes from Kenya Tax Base's supply classification --
the same table the VAT3 return reads -- so a sale cannot transmit under one
band and report in another box. The classification is asked by the tax's base
tag first (what an already-posted line will report as) and by its KRA item
code second, through x_ke_base's translation of Odoo's item-code letters,
which collide with KRA's.

Withholding taxes have no band and get none: withholding settles an invoice,
it is not a kind of supply.
"""

from odoo import api, fields, models

from .ke_etims_code import CODE_CLASS_TAX_TYPE


class AccountTax(models.Model):
    _inherit = "account.tax"

    ke_etims_tax_type_id = fields.Many2one(
        "ke.etims.code", string="KRA Tax Type",
        domain=[("code_class", "=", CODE_CLASS_TAX_TYPE)],
        compute="_compute_ke_etims_tax_type_id", store=True, readonly=False,
        help="The KRA tax type this tax transmits as on eTIMS. Defaulted from "
             "the Kenyan supply classification; empty for taxes that are not "
             "a VAT band, such as withholding.")

    @api.depends("country_code", "type_tax_use", "amount",
                 "invoice_repartition_line_ids.tag_ids", "l10n_ke_item_code_id")
    def _compute_ke_etims_tax_type_id(self):
        for tax in self:
            if tax.ke_etims_tax_type_id or tax.country_code != "KE":
                continue
            band = tax._ke_etims_band()
            if band:
                tax.ke_etims_tax_type_id = self.env["ke.etims.code"]._ke_etims_find(
                    CODE_CLASS_TAX_TYPE, band)

    def _ke_etims_band(self):
        """The KRA band letter x_ke_base classifies this tax as, or False."""
        self.ensure_one()
        Classification = self.env["ke.tax.classification"].sudo()
        base_lines = self.invoice_repartition_line_ids.filtered(
            lambda line: line.repartition_type == "base")
        for tag in base_lines.tag_ids:
            band = Classification.band_for_tag(tag.name)
            if band:
                return band
        if self.l10n_ke_item_code_id.tax_rate:
            return Classification.band_for_item_rate(self.l10n_ke_item_code_id.tax_rate)
        return False

    @api.model
    def _ke_etims_fill_tax_types(self):
        """Default the tax type of Kenyan taxes that have none yet -- run after a
        code sync, since before it there are no tax-type rows to point at."""
        taxes = self.with_context(active_test=False).search([
            ("country_code", "=", "KE"), ("ke_etims_tax_type_id", "=", False),
            ("type_tax_use", "in", ("sale", "purchase")),
        ])
        for tax in taxes:
            band = tax._ke_etims_band()
            if band:
                code = self.env["ke.etims.code"]._ke_etims_find(CODE_CLASS_TAX_TYPE, band)
                if code:
                    tax.ke_etims_tax_type_id = code
