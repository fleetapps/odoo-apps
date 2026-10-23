import re
from urllib.parse import quote, urlencode

from markupsafe import Markup

from odoo import _, api, fields, models


class OnlineQuoteShare(models.TransientModel):
    """Send the online quote page on WhatsApp: to a lead, filled in for them,
    or as the agent's own link to anyone -- a chat, a group, a status. Either
    way the quotes made through it come back to the agent."""

    _name = "odin.medical.quote.online.share"
    _description = "Share the online medical quote page"

    lead_id = fields.Many2one("crm.lead", string="For", readonly=True)
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    url = fields.Char(string="Link", compute="_compute_url")
    phone = fields.Char(compute="_compute_phone")
    message = fields.Text(compute="_compute_message", store=True, readonly=False)
    conversation_id = fields.Many2one("discuss.channel", compute="_compute_conversation")

    def _link(self, **params):
        self.ensure_one()
        online = self.env["odin.medical.quote.online"]
        query = {"agent": online.agent_token(self.user_id)}
        if self.lead_id:
            query["lead"] = online.lead_token(self.lead_id)
        query.update(params)
        return "%s/medical-quote?%s" % (self.get_base_url(), urlencode(query))

    @api.depends("lead_id", "user_id")
    def _compute_url(self):
        for wizard in self:
            wizard.url = wizard._link()

    @api.depends("lead_id")
    def _compute_phone(self):
        for wizard in self:
            lead = wizard.lead_id
            raw = lead.partner_id.phone or lead.phone or ""
            digits = re.sub(r"\D", "", raw)
            code = str((lead.partner_id.country_id or lead.country_id
                        or wizard.env.company.country_id).phone_code or "")
            if digits and code and not raw.strip().startswith("+") and not digits.startswith(code):
                digits = code + digits.lstrip("0")
            wizard.phone = digits

    @api.depends("lead_id", "user_id")
    def _compute_message(self):
        for wizard in self:
            name = (wizard.lead_id.partner_id.name or wizard.lead_id.contact_name or "").split(" ")[0]
            wizard.message = _(
                "%(hello)s you can see medical cover prices for your family and get your "
                "quote here, in about two minutes:\n%(link)s",
                hello=_("Hello %s,", name) if name else _("Hello,"),
                link=wizard._link(utm_source="WhatsApp", utm_medium="Chat"),
            )

    @api.depends("lead_id")
    def _compute_conversation(self):
        Channel = self.env["discuss.channel"]
        for wizard in self:
            partner = wizard.lead_id.partner_id
            if partner and "wa_partner_id" in Channel._fields:
                wizard.conversation_id = Channel.search([
                    ("channel_type", "=", "whatsapp"), ("wa_partner_id", "=", partner.id)],
                    order="id desc", limit=1)
            else:
                wizard.conversation_id = False

    def _log(self):
        if self.lead_id:
            self.lead_id.message_post(body=_("Online quote link sent on WhatsApp."),
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
