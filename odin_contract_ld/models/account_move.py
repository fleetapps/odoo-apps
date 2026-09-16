# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    odin_ld_charge_ids = fields.One2many(
        "odin.ld.charge",
        "move_id",
        string="Liquidated Damages",
        readonly=True,
    )
    odin_ld_charge_count = fields.Integer(compute="_compute_odin_ld_charge_count")
    odin_ld_amount = fields.Monetary(
        string="Damages Deducted",
        compute="_compute_odin_ld_amount",
        currency_field="currency_id",
        help="Total liquidated damages deducted on this document.",
    )

    @api.depends("odin_ld_charge_ids")
    def _compute_odin_ld_charge_count(self):
        for move in self:
            move.odin_ld_charge_count = len(move.odin_ld_charge_ids)

    @api.depends("odin_ld_charge_ids.amount_net")
    def _compute_odin_ld_amount(self):
        for move in self:
            move.odin_ld_amount = sum(move.odin_ld_charge_ids.mapped("amount_net"))

    def action_view_odin_ld_charges(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Liquidated Damages"),
            "res_model": "odin.ld.charge",
            "view_mode": "list,form",
            "domain": [("move_id", "=", self.id)],
        }

    def button_draft(self):
        """Resetting to draft must not silently orphan an applied charge."""
        res = super().button_draft()
        charges = self.odin_ld_charge_ids.filtered(lambda c: c.state == "applied")
        if charges:
            charges.message_post(
                body=self.env._(
                    "The document this charge was deducted on was reset to draft."
                )
            )
        return res


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    odin_ld_charge_id = fields.Many2one(
        "odin.ld.charge",
        string="Damages Charge",
        readonly=True,
        ondelete="set null",
        index="btree_not_null",
        copy=False,
    )
