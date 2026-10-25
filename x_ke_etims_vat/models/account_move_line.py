# -*- coding: utf-8 -*-
"""The eTIMS number beside each journal item a VAT box drills down to."""

from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    ke_etims_number = fields.Char(
        string="eTIMS No.", compute="_compute_ke_etims_number",
        help="The CU invoice number KRA knows the document by: ours on a sale, "
             "the supplier's on a bill.")

    @api.depends("move_id.ke_etims_cu_invoice", "move_id.ke_etims_supplier_invoice_ref")
    def _compute_ke_etims_number(self):
        for line in self:
            line.ke_etims_number = line.move_id and line.move_id._ke_etims_number()

    @api.model
    def _get_view_cache_key(self, view_id=None, view_type="form", **options):
        key = super()._get_view_cache_key(view_id, view_type, **options)
        return key + (self.env["res.company"]._ke_etims_any_kenyan(self.env.companies),)

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        """The tax audit list is every country's: leave it as it was for
        someone who works in no Kenyan company."""
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == "list" \
                and not self.env["res.company"]._ke_etims_any_kenyan(self.env.companies):
            for node in arch.xpath("//field[@name='ke_etims_number']"):
                node.getparent().remove(node)
        return arch, view
