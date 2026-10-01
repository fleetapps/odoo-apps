from odoo import api, fields, models


class WhatsappRoutingConfig(models.Model):
    """Lead Routing state of one account (SPEC.md §39, §41, §53.1).

    The mode lives on the account (C1) and the eligible users are its Notify
    users with routing enabled (C2); this record keeps the method, the fallback
    and the round-robin cursor, whose row is locked while it advances (R29).
    """

    _name = "whatsapp_connector.routing.config"
    _description = "WhatsApp Routing Configuration"

    account_id = fields.Many2one(
        "whatsapp_connector.account", required=True, ondelete="cascade", index=True,
    )
    method = fields.Selection([("round_robin", "Round Robin")], default="round_robin", required=True)
    fallback_user_id = fields.Many2one(
        "res.users", "Fallback User", ondelete="set null", domain="[('share', '=', False)]",
        help="Gets new conversations when no eligible user is available. Without one, they wait "
        "unassigned and WhatsApp managers are notified.",
    )
    last_assigned_user_id = fields.Many2one(
        "res.users", "Last Assigned", readonly=True, ondelete="set null",
        help="Round-robin cursor: the next conversation goes to the user after this one.",
    )

    _account_unique = models.Constraint(
        "UNIQUE(account_id)", "An account has one routing configuration.",
    )

    @api.model
    def _wa_can_own(self, user, account):
        """Whether ``user`` may own a conversation of ``account`` at all (§17)."""
        return bool(
            user
            and user.active
            and not user.share
            and user.has_group("whatsapp_connector.group_whatsapp_user")
            and account.company_id in user.company_ids,
        )

    def _wa_routing_operators(self):
        """The account's Notify users taking part in routing, in round-robin order."""
        self.ensure_one()
        return self.account_id.operator_ids.filtered("routing_enabled").sorted(
            lambda operator: (operator.sequence, operator.id),
        )

    def _wa_eligible_users(self):
        self.ensure_one()
        operators = self._wa_routing_operators().filtered("active")
        return operators.user_id.filtered(lambda user: self._wa_can_own(user, self.account_id))

    def _wa_next_user(self):
        """Round-robin (§16, §41): the user after the last assigned one, skipping the ineligible.

        ``A → B → C → A``, and with B disabled ``A → C → A``. The cursor row is
        locked first (R29): a concurrent routing gets ``LockError`` and is
        retried. Without any eligible user, the fallback user, else nobody (§40).
        """
        self.ensure_one()
        self.lock_for_update()
        eligible = self._wa_eligible_users()
        users = self._wa_routing_operators().user_id
        if eligible:
            start = (list(users).index(self.last_assigned_user_id) + 1) if self.last_assigned_user_id in users else 0
            for offset in range(len(users)):
                user = users[(start + offset) % len(users)]
                if user in eligible:
                    self.last_assigned_user_id = user
                    return user
        if self._wa_can_own(self.fallback_user_id, self.account_id):
            return self.fallback_user_id
        return self.env["res.users"]
