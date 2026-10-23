from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    odin_online_user_id = fields.Many2one(
        "res.users", string="Assign online quotes to",
        domain=[("share", "=", False)],
        help="Who gets an online quote that came from an ad, the website or the plain "
        "page, when the client has no agent already. Left empty, nobody: the team is "
        "told and anyone can pick it up (Assign to me).",
    )
    odin_online_team_id = fields.Many2one(
        "crm.team", string="Online quotes sales team",
        help="The team online quotes' opportunities belong to.",
    )
    odin_online_whatsapp = fields.Char(
        string="WhatsApp number for clients",
        help="Opened by Chat with us on WhatsApp on the client's quote. Left "
        "empty, the company phone.",
    )
    odin_online_reply_time = fields.Char(
        string="We get back to clients", translate=True,
        default=lambda self: self.env._("within 2 working hours"),
        help="Finishes the sentence 'An advisor will contact you ...' on the "
        "client's quote.",
    )
    odin_online_privacy_url = fields.Char(
        string="Privacy policy link",
        help="Linked from the consent box on the online quote form.",
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odin_online_user_id = fields.Many2one(
        related="company_id.odin_online_user_id", readonly=False)
    odin_online_team_id = fields.Many2one(
        related="company_id.odin_online_team_id", readonly=False)
    odin_online_whatsapp = fields.Char(
        related="company_id.odin_online_whatsapp", readonly=False)
    odin_online_reply_time = fields.Char(
        related="company_id.odin_online_reply_time", readonly=False)
    odin_online_privacy_url = fields.Char(
        related="company_id.odin_online_privacy_url", readonly=False)
    odin_online_url = fields.Char(
        string="Medical quote page", compute="_compute_odin_online_url")

    @api.depends("company_id")
    def _compute_odin_online_url(self):
        for settings in self:
            settings.odin_online_url = "%s%s" % (
                settings.company_id.get_base_url(),
                self.env["odin.medical.quote.online"]._odin_path())
