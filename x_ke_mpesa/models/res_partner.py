# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    mpesa_account_ref = fields.Char(
        "M-Pesa account number", index="btree_not_null", copy=False,
        help="What this customer types as the paybill account number (a "
             "customer code, a unit or house number). Their receipts then pay "
             "their oldest open invoices.")
    mpesa_transaction_ids = fields.One2many("mpesa.transaction", "partner_id")
    mpesa_transaction_count = fields.Integer(compute="_compute_mpesa_transaction_count")

    def _compute_mpesa_transaction_count(self):
        groups = self.env["mpesa.transaction"]._read_group(
            [("partner_id", "child_of", self.ids), ("state", "=", "confirmed")],
            ["partner_id"], ["__count"])
        counts = {}
        for partner, count in groups:
            commercial = partner.commercial_partner_id
            counts[commercial.id] = counts.get(commercial.id, 0) + count
        for partner in self:
            partner.mpesa_transaction_count = counts.get(partner.commercial_partner_id.id, 0)

    def action_open_mpesa_payments(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "x_ke_mpesa.action_mpesa_report_partner")
        action["domain"] = [("partner_id", "child_of", self.commercial_partner_id.id),
                            ("state", "=", "confirmed")]
        return action
