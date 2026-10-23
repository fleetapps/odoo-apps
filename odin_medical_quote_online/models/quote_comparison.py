import secrets

from odoo import api, fields, models
from odoo.tools import consteq

CONTACT_BY = [("whatsapp", "WhatsApp"), ("call", "Phone call"), ("email", "Email")]
CONTACT_WHEN = [("any", "Any time"), ("morning", "Morning"),
                ("afternoon", "Afternoon"), ("evening", "Evening")]


class QuoteComparison(models.Model):
    _inherit = "odin.quote.comparison"

    odin_channel = fields.Selection(
        [("agent", "Agent"), ("online", "Online")], string="Channel",
        default="agent", required=True, index=True, copy=False,
        help="Online: made by the client on the online quote page.")
    odin_access_token = fields.Char(copy=False, readonly=True, groups="base.group_user")
    odin_contact_by = fields.Selection(CONTACT_BY, string="Contact by", copy=False)
    odin_contact_when = fields.Selection(CONTACT_WHEN, string="Best time", copy=False)
    odin_callback_requested = fields.Datetime(
        string="Asked for a call", copy=False, readonly=True,
        help="When the client pressed Talk to an advisor on their quote.")
    odin_online_url = fields.Char(
        string="Client's link", compute="_compute_odin_online_url",
        help="The client's own page for this quote: the plans in plain words, "
        "the PDF, Talk to an advisor.")

    @api.depends("odin_access_token")
    def _compute_odin_online_url(self):
        for comparison in self:
            comparison.odin_online_url = (
                comparison._odin_online_url() if comparison.odin_access_token else False)

    def _odin_online_ensure_token(self):
        for comparison in self.sudo().filtered(lambda c: not c.odin_access_token):
            comparison.odin_access_token = secrets.token_urlsafe(24)
        return self

    def _odin_online_url(self, suffix=""):
        self.ensure_one()
        self._odin_online_ensure_token()
        return "%s/medical-quote/q/%s%s?token=%s" % (
            self.get_base_url(), self.id, suffix, self.sudo().odin_access_token)

    def _odin_online_check(self, token):
        """Whether ``token`` opens this quote's page."""
        self.ensure_one()
        stored = self.sudo().odin_access_token
        return bool(stored and token and consteq(stored, str(token)))

    def action_odin_online_link(self):
        """Make the client's link, so it can be copied and sent."""
        self._odin_online_ensure_token()
        return True
