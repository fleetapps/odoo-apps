import re
from urllib.parse import quote, urlencode

from markupsafe import Markup

from odoo import _, api, fields, models


class OnlineQuoteShare(models.TransientModel):
    """Share an online quote page -- medical, motor -- three ways: to a lead,
    filled in for them; as the agent's own link to anyone (a chat, a group,
    a status), the quotes coming back to the agent; or as a campaign link for
    an ad or the website, the quotes going to the team to pick up."""

    _name = "odin.medical.quote.online.share"
    _description = "Share an online quote page"

    quote_type = fields.Selection(
        [("medical", "Medical")], string="Quote for", required=True, default="medical")
    audience = fields.Selection(
        [("mine", "Anyone I send it to"), ("campaign", "An ad, the website or a campaign")],
        string="Who it's for", required=True, default="mine")
    link_for = fields.Selection(
        [("mine", "My own link"), ("client", "This client"), ("campaign", "Campaign")],
        compute="_compute_link_for")
    lead_id = fields.Many2one("crm.lead", string="Client", readonly=True)
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    source_id = fields.Many2one(
        "utm.source", string="Where it runs",
        help="Facebook, Instagram, Google, Website... The opportunity's source, so CRM "
        "can tell which ads bring quotes.")
    campaign_id = fields.Many2one(
        "utm.campaign", string="Campaign", help="Optional: the ad campaign, e.g. 'Family cover October'.")
    url = fields.Char(string="Link", compute="_compute_url")
    phone = fields.Char(compute="_compute_phone")
    message = fields.Text(compute="_compute_message", store=True, readonly=False)
    conversation_id = fields.Many2one("discuss.channel", compute="_compute_conversation")
    routing = fields.Char(compute="_compute_routing")

    @api.depends("lead_id", "audience")
    def _compute_link_for(self):
        for wizard in self:
            wizard.link_for = "client" if wizard.lead_id else wizard.audience

    @api.model
    def _odin_type_for_lead(self, lead):
        """Motor for a lead tagged Motor, where motor quotes are online."""
        types = dict(self._fields["quote_type"].selection)
        names = set(lead.tag_ids.mapped("name"))
        return next((key for key, label in types.items() if label in names), "medical")

    def _online(self):
        self.ensure_one()
        models = self.env["odin.quote.comparison"]._odin_online_models()
        return self.env[models.get(self.quote_type) or models["medical"]]

    def _link(self, **params):
        self.ensure_one()
        online = self._online()
        query = {}
        if self.link_for == "campaign":
            # No agent: a quote from an ad belongs to whoever picks it up.
            if self.source_id:
                query["utm_source"] = self.source_id.name
            if self.campaign_id:
                query["utm_campaign"] = self.campaign_id.name
        else:
            query["agent"] = online.agent_token(self.user_id)
            if self.link_for == "client" and self.lead_id:
                query["lead"] = online.lead_token(self.lead_id)
        query.update(params)
        return "%s%s%s" % (self.get_base_url(), online._odin_path(),
                           "?%s" % urlencode(query) if query else "")

    @api.depends("quote_type", "lead_id", "audience", "user_id", "source_id", "campaign_id")
    def _compute_url(self):
        for wizard in self:
            wizard.url = wizard._link()

    @api.depends("lead_id", "audience")
    def _compute_routing(self):
        user = self.env.company.odin_online_user_id
        for wizard in self:
            if wizard.link_for != "campaign":
                wizard.routing = False
            elif user:
                wizard.routing = _("Quotes made through it go to %s (Settings ▸ Online quotes), "
                                   "or to the client's own agent if they have one.", user.name)
            else:
                wizard.routing = _("Quotes made through it wait for anyone in the team to pick "
                                   "them up (Cover Quotes ▸ Online quotes ▸ Assign to me), or go "
                                   "to the client's own agent if they have one.")

    @api.depends("lead_id", "audience")
    def _compute_phone(self):
        for wizard in self:
            lead = wizard.lead_id if wizard.link_for == "client" else wizard.lead_id.browse()
            raw = lead.partner_id.phone or lead.phone or ""
            digits = re.sub(r"\D", "", raw)
            code = str((lead.partner_id.country_id or lead.country_id
                        or wizard.env.company.country_id).phone_code or "")
            if digits and code and not raw.strip().startswith("+") and not digits.startswith(code):
                digits = code + digits.lstrip("0")
            wizard.phone = digits

    @api.depends("quote_type", "lead_id", "audience", "user_id")
    def _compute_message(self):
        for wizard in self:
            if wizard.link_for == "campaign":
                wizard.message = False
                continue
            name = (wizard.lead_id.partner_id.name or wizard.lead_id.contact_name or "").split(" ")[0]
            wizard.message = wizard._online()._share_message(
                _("Hello %s,", name) if name and wizard.link_for == "client" else _("Hello,"),
                wizard._link(utm_source="WhatsApp", utm_medium="Chat"))

    @api.depends("lead_id", "audience")
    def _compute_conversation(self):
        Channel = self.env["discuss.channel"]
        for wizard in self:
            partner = wizard.lead_id.partner_id if wizard.link_for == "client" else False
            if partner and "wa_partner_id" in Channel._fields:
                wizard.conversation_id = Channel.search([
                    ("channel_type", "=", "whatsapp"), ("wa_partner_id", "=", partner.id)],
                    order="id desc", limit=1)
            else:
                wizard.conversation_id = False

    def _log(self):
        if self.lead_id and self.link_for == "client":
            self.lead_id.message_post(body=_(
                "%s online quote link sent on WhatsApp.",
                dict(self._fields["quote_type"].selection)[self.quote_type]),
                subtype_xmlid="mail.mt_note")

    def action_whatsapp(self):
        """Open WhatsApp with the message ready: to the lead's number, or to
        whoever the agent picks."""
        self.ensure_one()
        self._log()
        return {
            "type": "ir.actions.act_url",
            "url": "https://wa.me/%s?text=%s" % (self.phone or "", quote(self.message or "")),
            "target": "new",
        }

    def action_send_in_conversation(self):
        """Post it in the client's WhatsApp conversation, from the company's
        number (whatsapp_connector)."""
        self.ensure_one()
        self.conversation_id.message_post(
            body=Markup("<p>%s</p>") % (self.message or ""), message_type="comment")
        self._log()
        return {
            "type": "ir.actions.act_window",
            "res_model": "discuss.channel",
            "res_id": self.conversation_id.id,
            "view_mode": "form",
        }
