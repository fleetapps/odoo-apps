# -*- coding: utf-8 -*-
"""The KRA VAT special table, as a flag on the supplier.

iTax restricts certain non-compliant VAT-registered taxpayers from having their
invoices claimed as input tax by their customers. KRA publishes them as the "VAT
special table", and a return carrying one of their invoices is rejected at
filing with a message naming the supplier.

There is no API for the table, so it is maintained by hand. Flagging the partner
rather than the individual invoice is deliberate: the restriction attaches to the
supplier for a period, so one flag correctly excludes every invoice they have
issued. The restriction is relaxed for credit notes, which is handled where the
reconciliation classifies a line, not here.
"""

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    ke_vat_special_table = fields.Boolean(
        string="On KRA VAT Special Table",
        help="KRA restricts input tax claims against this supplier. Their "
             "invoices are excluded from the claim when a VAT return is "
             "reconciled; their credit notes are not.",
    )
    ke_vat_special_table_date = fields.Date(
        string="Special Table Since",
        help="When KRA listed this supplier. Kept for audit -- it is the "
             "evidence for input tax not claimed in that period.",
    )
