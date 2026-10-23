import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import consteq

CONTACT_BY = [("whatsapp", "WhatsApp"), ("call", "Phone call"), ("email", "Email")]
CONTACT_WHEN = [("any", "Any time"), ("morning", "Morning"),
                ("afternoon", "Afternoon"), ("evening", "Evening")]


class QuoteComparison(models.Model):
    _inherit = "odin.quote.comparison"

    odin_channel = fields.Selection(
        [("agent", "Sales team"), ("online", "Online")], string="Channel",
        default="agent", required=True, index=True, copy=False,
        help="Online: made by the client on an online quote page.")
    odin_online_via = fields.Selection(
        [("agent", "An agent's link"), ("client", "A link sent to the client"),
         ("public", "Ad, website or shared page")],
        string="Came through", copy=False, readonly=True,
        help="How the client reached the online quote page: an agent's own link and a "
        "link sent from their opportunity go to that agent; an ad, the website or the "
        "plain page go to the team.")
    odin_source_id = fields.Many2one(
        related="opportunity_id.source_id", string="Source", store=True)
    odin_campaign_id = fields.Many2one(
        related="opportunity_id.campaign_id", string="Campaign", store=True)
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

    def _odin_online_models(self):
        """The online page of each type of quote (motor adds its own)."""
        return {"medical": "odin.medical.quote.online"}

    def _odin_online(self):
        self.ensure_one()
        models = self._odin_online_models()
        return self.env[models.get(self.odin_quote_type) or models["medical"]]

    def _odin_online_url(self, suffix=""):
        self.ensure_one()
        self._odin_online_ensure_token()
        return "%s%s/q/%s%s?token=%s" % (
            self.get_base_url(), self._odin_online().sudo()._odin_path(), self.id, suffix,
            self.sudo().odin_access_token)

    def _odin_online_check(self, token):
        """Whether ``token`` opens this quote's page."""
        self.ensure_one()
        stored = self.sudo().odin_access_token
        return bool(stored and token and consteq(stored, str(token)))

    def _whatsapp_page_url(self):
        """Share on WhatsApp links to the client's own quote page -- the plans
        in plain words on a phone, the PDF, Talk to an advisor -- rather than
        a bare PDF."""
        self.ensure_one()
        if self.odin_from_builder and self.plan_ids:
            return self._odin_online_url()
        return super()._whatsapp_page_url()

    def action_odin_pick_up(self):
        """Assign to me: take an online quote nobody has picked up yet, with
        its opportunity, its quotations and the to-do to contact the client."""
        self.ensure_one()
        if self.opportunity_id:
            return self.opportunity_id.action_odin_pick_up()
        if self.user_id and self.user_id != self.env.user:
            raise UserError(_("%s has already picked this up.", self.user_id.name))
        self.user_id = self.env.user
        return True

    def action_odin_online_link(self):
        """Make the client's link, so it can be copied and sent."""
        self._odin_online_ensure_token()
        return True
