import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# #RRGGBB: the accent colour goes into the document's styles as it is typed.
HEX_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}")


class ResCompany(models.Model):
    """What the letterhead and the payment block print, besides the company's
    own address, PIN and bank accounts."""

    _inherit = "res.company"

    doc_short_name = fields.Char(
        string="Short name on numbers", help="Starts internal document numbers, e.g. RINZ/MEMO/HR/2026/001.")
    doc_mpesa_paybill = fields.Char(string="M-Pesa paybill")
    doc_mpesa_account = fields.Char(string="M-Pesa account no.")
    doc_cheque_payee = fields.Char(string="Cheques payable to", help="Empty: the company's name.")
    doc_stamp = fields.Image(string="Company stamp", max_width=600, max_height=600,
                             help="Printed in the stamp box of documents that have one.")
    doc_replace_standard = fields.Boolean(
        string="These documents replace Odoo's", default=True,
        help="Print menus offer these documents instead of Odoo's own invoice, quotation, purchase order, "
             "delivery slip and count sheet, and invoices, quotations and purchase orders are emailed with them.")
    doc_export_currency_id = fields.Many2one(
        "res.currency", string="Export currency", default=lambda self: self.env.ref("base.USD", raise_if_not_found=False),
        help="The second currency of a commercial invoice raised in the company's own currency.")
    doc_export_conditions = fields.Char(string="Conditions of sale (export)", default="Ex-factory, collection by buyer")
    doc_freight_terms = fields.Char(string="Freight charges (export)", default="On buyer")
    doc_brand = fields.Char(string="Brand on export documents", help="When the product has no brand of its own.")
    doc_invoice_style = fields.Selection(
        [("letterhead", "Letterhead"), ("band", "Band")], string="Document style",
        default="letterhead", required=True,
        help="Letterhead: on the company's letterhead. Band, for the Sales Invoice and the Delivery Note: a "
             "shaded band across the top of each page, the company on the left (with an invoice's payment terms "
             "and CU number), the logo in the middle and the customer on the right; the title, the table's "
             "heading and the total in the accent colour. The other documents stay on the letterhead.")
    doc_accent_color = fields.Char(
        string="Accent colour", default="#B1A58E",
        help="The Band invoice's title, table heading and total. A colour such as #B1A58E.")

    @api.constrains("doc_accent_color")
    def _check_doc_accent_color(self):
        for company in self:
            if company.doc_accent_color and not HEX_COLOUR.fullmatch(company.doc_accent_color):
                raise ValidationError(_("The accent colour is a colour such as #B1A58E: # and six hex digits."))

    def _doc_accent(self):
        self.ensure_one()
        colour = self.doc_accent_color or ""
        return colour if HEX_COLOUR.fullmatch(colour) else "#B1A58E"

    def _doc_short(self):
        self.ensure_one()
        return self.doc_short_name or ((self.name or "").split() or ["DOC"])[0].upper()

    def action_odin_documents_settings(self):
        return {
            "type": "ir.actions.act_window", "res_model": "res.company", "res_id": self.env.company.id,
            "view_mode": "form", "views": [(self.env.ref("base.view_company_form").id, "form")],
            "target": "current", "context": {"odin_documents_tab": True},
        }


class HrDepartment(models.Model):
    _inherit = "hr.department"

    doc_code = fields.Char(string="Code on document numbers", help="e.g. HR, PRD, FIN: RINZ/MEMO/HR/2026/001.")

    def _doc_code(self):
        self.ensure_one()
        return (self.doc_code or "".join(word[0] for word in (self.name or "X").split())[:3]).upper()
