from odoo import api, fields, models
from odoo.exceptions import UserError


class WaAssign(models.TransientModel):
    """Hand a WhatsApp conversation to a colleague."""

    _name = "odin.wa.assign"
    _description = "Assign a WhatsApp Conversation"

    channel_id = fields.Many2one(
        "discuss.channel", "Conversation", required=True, readonly=True, ondelete="cascade",
    )
    current_user_id = fields.Many2one(
        related="channel_id.wa_assigned_user_id", string="Currently with", readonly=True,
    )
    eligible_user_ids = fields.Many2many(
        "res.users", compute="_compute_eligible_user_ids",
        help="The account's rota. Anyone outside it would lose the conversation again "
        "on the client's next message.",
    )
    user_id = fields.Many2one("res.users", "Assign to", required=True)

    @api.depends("channel_id")
    def _compute_eligible_user_ids(self):
        # the stored link, not _wa_routing_config(): that creates the config when
        # it is missing, and a compute must not write
        for wizard in self:
            config = wizard.channel_id.sudo().wa_account_id.routing_config_id
            wizard.eligible_user_ids = config._wa_eligible_users() if config else False

    def action_assign(self):
        self.ensure_one()
        channel = self.channel_id.sudo()
        if not channel.wa_routed:
            raise UserError(self.env._("This conversation has no owner to change."))
        if self.user_id not in self.eligible_user_ids:
            raise UserError(self.env._(
                "%s does not take part in this number's routing, so the client's next "
                "message would take the conversation away again.", self.user_id.name,
            ))
        # the single assignment path: it moves the lead's salesperson, the Discuss
        # membership and the audit note together
        channel._wa_assign(self.user_id, reason="manager")
        return {"type": "ir.actions.act_window_close"}
