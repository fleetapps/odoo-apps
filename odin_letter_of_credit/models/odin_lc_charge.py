# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models

CHARGE_TYPE = [
    ("opening", "Opening / Issuance"),
    ("amendment", "Amendment"),
    ("confirmation", "Confirmation"),
    ("negotiation", "Negotiation"),
    ("acceptance", "Acceptance"),
    ("discrepancy", "Discrepancy"),
    ("courier", "Courier"),
    ("other", "Other"),
]


class OdinLcCharge(models.Model):
    """Bank charges on a credit.

    Kept as their own records rather than buried in the bills, because on an
    import credit these are a real cost of the project and belong on the same
    analytic account as the goods.
    """

    _name = "odin.lc.charge"
    _description = "Letter of Credit Bank Charge"
    _order = "date desc, id desc"
    _inherit = ["analytic.mixin"]

    name = fields.Char(required=True)
    lc_id = fields.Many2one(
        "odin.letter.of.credit", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="lc_id.company_id", store=True)
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
        help="Bank charges are often billed in a different currency from the "
        "credit itself, so this is not simply inherited.",
    )
    charge_type = fields.Selection(CHARGE_TYPE, required=True, default="opening")
    date = fields.Date(required=True, default=fields.Date.context_today)
    amount = fields.Monetary(required=True)
    partner_id = fields.Many2one("res.partner", string="Bank")
    move_id = fields.Many2one(
        "account.move", string="Bill", index="btree_not_null",
        help="The bank's debit advice or bill, once posted.",
    )
    note = fields.Text()

    _amount_positive = models.Constraint(
        "CHECK(amount >= 0)", "A bank charge cannot be negative."
    )

    @api.onchange("lc_id")
    def _onchange_lc_id(self):
        if self.lc_id:
            if not self.analytic_distribution:
                self.analytic_distribution = self.lc_id.analytic_distribution
            if not self.partner_id:
                self.partner_id = self.lc_id.issuing_bank_id
