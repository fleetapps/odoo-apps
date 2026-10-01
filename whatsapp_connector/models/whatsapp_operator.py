from odoo import fields, models


class WhatsappOperator(models.Model):
    """One of the account's Notify users (SPEC.md §4, R3).

    It is the only per-account list of users (C2): Lead Routing picks among the
    ones with routing enabled (§39).
    """

    _name = "whatsapp_connector.operator"
    _description = "WhatsApp Notify User"
    _order = "account_id, sequence, id"

    account_id = fields.Many2one(
        "whatsapp_connector.account", required=True, ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10, help="Order used by round-robin routing.")
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade",
        domain="[('share', '=', False)]",
    )
    active = fields.Boolean(default=True)
    routing_enabled = fields.Boolean(
        "Routing Enabled", default=True,
        help="In Lead Routing, new conversations can be assigned to this user.",
    )

    _account_user_unique = models.Constraint(
        "UNIQUE(account_id, user_id)",
        "This user is already a Notify user of this account.",
    )
