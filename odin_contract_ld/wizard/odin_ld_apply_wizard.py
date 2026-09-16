# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models
from odoo.exceptions import UserError

# Which document a charge can be deducted from depends on who owes whom.
MOVE_TYPE_BY_DIRECTION = {
    "payable": ("out_invoice", "out_refund"),
    "receivable": ("in_invoice", "in_refund"),
}


class OdinLdApplyWizard(models.TransientModel):
    _name = "odin.ld.apply.wizard"
    _description = "Deduct Liquidated Damages from a Document"

    charge_id = fields.Many2one("odin.ld.charge", required=True, readonly=True)
    agreement_id = fields.Many2one(related="charge_id.agreement_id")
    direction = fields.Selection(related="charge_id.direction")
    partner_id = fields.Many2one(related="charge_id.partner_id")
    currency_id = fields.Many2one(related="charge_id.currency_id")
    company_id = fields.Many2one(related="charge_id.company_id")
    amount_available = fields.Monetary(
        related="charge_id.amount_net",
        string="Net Charge",
    )
    move_id = fields.Many2one(
        "account.move",
        string="Deduct From",
        required=True,
        help="The draft invoice or bill the deduction line is added to.",
    )
    move_type_domain = fields.Char(compute="_compute_move_type_domain")
    amount = fields.Monetary(required=True)
    account_id = fields.Many2one(
        "account.account",
        string="Damages Account",
        required=True,
        domain="[('deprecated', '=', False)]",
    )
    label = fields.Char(
        string="Line Description",
        required=True,
        help="What the counterparty will read on the document.",
    )
    analytic_distribution = fields.Json()

    @api.depends("direction")
    def _compute_move_type_domain(self):
        for wizard in self:
            wizard.move_type_domain = str(
                list(MOVE_TYPE_BY_DIRECTION.get(wizard.direction, ()))
            )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        charge_id = values.get("charge_id") or self.env.context.get("default_charge_id")
        if not charge_id:
            return values
        charge = self.env["odin.ld.charge"].browse(charge_id)
        values.setdefault("amount", charge.amount_net)
        values.setdefault("account_id", charge.account_id.id)
        values.setdefault("analytic_distribution", charge.analytic_distribution)
        values.setdefault(
            "label",
            self.env._(
                "Liquidated damages %(ref)s (%(days)s days, %(from)s to %(to)s)",
                ref=charge.name,
                days=charge.days_charged,
                **{
                    "from": charge.date_from,
                    "to": charge.date_to,
                },
            ),
        )
        return values

    @api.onchange("move_id")
    def _onchange_move_id(self):
        """Offer only documents the deduction can legally land on."""
        if not self.charge_id:
            return
        types = MOVE_TYPE_BY_DIRECTION.get(self.direction, ())
        return {
            "domain": {
                "move_id": [
                    ("move_type", "in", list(types)),
                    ("state", "=", "draft"),
                    ("partner_id", "=", self.partner_id.id),
                    ("company_id", "=", self.company_id.id),
                    ("currency_id", "=", self.currency_id.id),
                ]
            }
        }

    def _check_move(self):
        self.ensure_one()
        move = self.move_id
        types = MOVE_TYPE_BY_DIRECTION.get(self.direction, ())
        if move.state != "draft":
            raise UserError(
                self.env._(
                    "%(move)s is not a draft. A deduction cannot be added to a "
                    "posted document — credit it instead.",
                    move=move.display_name,
                )
            )
        if move.move_type not in types:
            raise UserError(
                self.env._(
                    "Damages in this direction are deducted from a different kind "
                    "of document, so %(move)s cannot carry them.",
                    move=move.display_name,
                )
            )
        if move.partner_id != self.partner_id:
            raise UserError(
                self.env._(
                    "%(move)s is addressed to a different counterparty.",
                    move=move.display_name,
                )
            )
        if move.currency_id != self.currency_id:
            raise UserError(
                self.env._(
                    "%(move)s is in %(move_currency)s but the agreement is in "
                    "%(agreement_currency)s. Converting a contractual figure "
                    "silently is not something this should guess at.",
                    move=move.display_name,
                    move_currency=move.currency_id.name,
                    agreement_currency=self.currency_id.name,
                )
            )
        if self.amount <= 0:
            raise UserError(self.env._("The deduction must be greater than zero."))
        if self.currency_id.compare_amounts(self.amount, self.amount_available) > 0:
            raise UserError(
                self.env._(
                    "You cannot deduct more than the net charge of %(net)s.",
                    net=self.amount_available,
                )
            )

    def action_apply(self):
        self.ensure_one()
        self._check_move()
        charge = self.charge_id
        if charge.state != "approved":
            raise UserError(
                self.env._("Only an approved charge can be deducted.")
            )
        # A deduction is a negative line rather than a separate journal entry,
        # because the counterparty has to SEE it on the document they are paying
        # against. That is the whole point of a certified deduction.
        line = self.env["account.move.line"].create(
            {
                "move_id": self.move_id.id,
                "name": self.label,
                "account_id": self.account_id.id,
                "quantity": 1.0,
                "price_unit": -self.amount,
                "display_type": "product",
                "analytic_distribution": self.analytic_distribution or False,
                "odin_ld_charge_id": charge.id,
                "tax_ids": [fields.Command.clear()],
            }
        )
        charge.write(
            {
                "state": "applied",
                "move_id": self.move_id.id,
                "move_line_id": line.id,
            }
        )
        charge.message_post(
            body=self.env._(
                "Deducted %(amount)s on %(move)s.",
                amount=self.amount,
                move=self.move_id.display_name,
            )
        )
        self.move_id.message_post(
            body=self.env._(
                "Liquidated damages %(ref)s deducted: %(amount)s.",
                ref=charge.name,
                amount=self.amount,
            )
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }
