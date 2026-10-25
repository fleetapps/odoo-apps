# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, fields, models
from odoo.exceptions import UserError


class MpesaShortcode(models.Model):
    _inherit = "mpesa.shortcode"

    pos_config_ids = fields.Many2many(
        "pos.config", "mpesa_shortcode_pos_config_rel", "shortcode_id", "config_id",
        string="Allowed points of sale",
        help="The points of sale that may collect into this till. Empty: any "
             "point of sale its payment method is added to.")
    pos_payment_method_ids = fields.One2many("pos.payment.method", "mpesa_shortcode_id")

    def action_create_pos_payment_method(self):
        """One POS payment method for this till, wired to its journal."""
        self.ensure_one()
        if not self.journal_id:
            raise UserError(_("Set up the accounting of %s first.", self.display_name))
        outstanding = self.journal_id.inbound_payment_method_line_ids.payment_account_id[:1]
        method = self.env["pos.payment.method"].create({
            "name": _("M-Pesa %s", self.number),
            "company_id": self.company_id.id,
            "journal_id": self.journal_id.id,
            "outstanding_account_id": outstanding.id,
            "payment_method_type": "terminal",
            "use_payment_terminal": "mpesa",
            "mpesa_shortcode_id": self.id,
            "config_ids": [(6, 0, self.pos_config_ids.ids)],
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "pos.payment.method",
            "res_id": method.id,
            "view_mode": "form",
        }
