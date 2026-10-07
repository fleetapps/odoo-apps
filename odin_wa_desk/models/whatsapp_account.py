from odoo import fields, models

#: Seeding only: what a number starts pointed at. Never consulted when a request
#: is logged -- a name resolved at runtime stops matching the day somebody renames
#: the record, and stops without raising anything.
DEFAULT_TEAM = "Client Service"
ARRIVED_BY = "WhatsApp"
#: WhatsApp stops free-form replies 24 hours after the client writes, so the
#: default leaves four hours to act on the reminder
UNANSWERED_HOURS = 20


class WhatsappAccount(models.Model):
    _inherit = "whatsapp_connector.account"

    odin_desk_team_id = fields.Many2one(
        "helpdesk.ticket.team", "Requests go to",
        default=lambda self: self._odin_default_desk_team(),
        help="The team a request logged from this number lands in. Whoever logs it can "
        "still move it.",
    )
    odin_desk_channel_id = fields.Many2one(
        "helpdesk.ticket.channel", "Mark requests as arriving by",
        default=lambda self: self._odin_default_desk_channel(),
        help="Recorded on the request, so phone, email and WhatsApp traffic can be told "
        "apart in reporting.",
    )
    odin_desk_unanswered_hours = fields.Integer(
        "Chase if unanswered after", default=UNANSWERED_HOURS,
        help="Hours after a client writes with no reply from anyone here before a "
        "reminder is raised. WhatsApp stops free replies at 24 hours, after which "
        "reaching them needs an approved template, so 20 leaves time to act. "
        "Set 0 to turn the reminder off.",
    )

    def _odin_default_desk_team(self):
        Team = self.env["helpdesk.ticket.team"]
        return Team.search([("name", "=", DEFAULT_TEAM)], limit=1) or Team.search(
            [], order="sequence, id", limit=1)

    def _odin_default_desk_channel(self):
        return self.env["helpdesk.ticket.channel"].search([("name", "=", ARRIVED_BY)], limit=1)

    def _odin_seed_desk_defaults(self):
        """Fill the Service Desk settings on numbers that predate this module.

        Field defaults cover new accounts. One that already existed would show
        the settings empty, and an empty setting reads the same as a deliberate
        one -- so it has to be filled, not left to be guessed at.
        """
        accounts = self.with_context(active_test=False).search([])
        team = self._odin_default_desk_team()
        channel = self._odin_default_desk_channel()
        for account in accounts:
            values = {}
            if team and not account.odin_desk_team_id:
                values["odin_desk_team_id"] = team.id
            if channel and not account.odin_desk_channel_id:
                values["odin_desk_channel_id"] = channel.id
            if values:
                account.write(values)
        return True
