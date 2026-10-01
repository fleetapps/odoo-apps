from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import plaintext2html

# Statuses Meta is known to send; anything else is shown as received (N2).
KNOWN_TEMPLATE_STATUSES = {
    "APPROVED": "Approved",
    "PENDING": "Pending",
    "REJECTED": "Rejected",
}


class WhatsappTemplate(models.Model):
    """A WhatsApp message template (SPEC.md §29, R25)."""

    _name = "whatsapp_connector.template"
    _description = "WhatsApp Template"
    _inherit = ["mail.thread"]
    _order = "name, id"

    name = fields.Char(required=True, tracking=True)
    template_name = fields.Char(
        "Template Name (Meta)", required=True, copy=False,
        help="The template's name at Meta: lowercase letters, digits and underscores.",
    )
    language_code = fields.Char("Language", required=True, default="en_US")
    account_id = fields.Many2one(
        "whatsapp_connector.account", "Account", required=True, ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    active = fields.Boolean(default=True)
    category = fields.Selection(
        [("marketing", "Marketing"), ("utility", "Utility"), ("authentication", "Authentication")],
        required=True, default="utility", tracking=True,
    )
    meta_template_id = fields.Char("Meta Template ID", readonly=True, copy=False)
    status = fields.Char(
        "Meta Status", readonly=True, copy=False, default="DRAFT",
        help="Exactly as Meta sends it; DRAFT until submitted.",
    )
    status_label = fields.Char("Status", compute="_compute_status_label")
    quality = fields.Char(readonly=True, copy=False)
    model_id = fields.Many2one(
        "ir.model", "Applies to", ondelete="cascade",
        domain=[("transient", "=", False)],
        help="The Odoo model this template is sent from; its fields fill the variables.",
    )
    model = fields.Char(related="model_id.model", store=True)
    phone_field = fields.Char(
        "Phone Field", default="partner_id",
        help="Field of the model that gives the recipient: a phone field, or a contact field "
        "whose phone is used.",
    )
    user_ids = fields.Many2many(
        "res.users", string="Users", domain="[('share', '=', False)]",
        help="Users allowed to send this template. Empty: every WhatsApp user.",
    )
    header_type = fields.Selection(
        [("none", "None"), ("text", "Text"), ("image", "Image"), ("video", "Video"),
         ("document", "Document"), ("location", "Location")],
        default="none", required=True,
    )
    header_text = fields.Char()
    header_attachment_id = fields.Many2one("ir.attachment", ondelete="set null")
    body = fields.Text()
    footer = fields.Char()
    button_ids = fields.One2many("whatsapp_connector.template.button", "template_id", copy=True)
    variable_ids = fields.One2many("whatsapp_connector.template.variable", "template_id", copy=True)

    _account_name_lang_unique = models.Constraint(
        "UNIQUE(account_id, template_name, language_code)",
        "A template with this name and language already exists for this account.",
    )

    @api.depends("status")
    def _compute_status_label(self):
        for template in self:
            status = template.status or ""
            template.status_label = KNOWN_TEMPLATE_STATUSES.get(status, status.title() or False)

    # ------------------------------------------------------------------
    # Sync from Meta (R25)
    # ------------------------------------------------------------------

    @api.model
    def _wa_sync_account(self, account, meta_templates):
        """Create or update the account's templates from Meta's template list (R25)."""
        existing = self.with_context(active_test=False).search([("account_id", "=", account.id)])
        by_id = {t.meta_template_id: t for t in existing if t.meta_template_id}
        by_name = {(t.template_name, t.language_code): t for t in existing}
        count = 0
        for data in meta_templates:
            name, language = data.get("name"), data.get("language")
            if not name or not language:
                continue
            vals = self._wa_values_from_meta(data)
            template = by_id.get(data.get("id")) or by_name.get((name, language))
            if template:
                template.write(vals)
            else:
                vals.update({
                    "name": name.replace("_", " ").capitalize(),
                    "template_name": name,
                    "language_code": language,
                    "account_id": account.id,
                })
                template = self.create(vals)
            count += 1
        return count

    @api.model
    def _wa_values_from_meta(self, data):
        vals = {
            "meta_template_id": data.get("id") or False,
            "status": data.get("status") or False,
        }
        category = (data.get("category") or "").lower()
        if category in ("marketing", "utility", "authentication"):
            vals["category"] = category
        quality = data.get("quality_score")
        if quality:
            vals["quality"] = quality.get("score") if isinstance(quality, dict) else str(quality)
        buttons = []
        for component in data.get("components") or []:
            kind = (component.get("type") or "").upper()
            if kind == "BODY":
                vals["body"] = component.get("text") or ""
            elif kind == "FOOTER":
                vals["footer"] = component.get("text") or False
            elif kind == "HEADER":
                header_format = (component.get("format") or "TEXT").lower()
                if header_format in ("text", "image", "video", "document", "location"):
                    vals["header_type"] = header_format
                vals["header_text"] = component.get("text") or False
            elif kind == "BUTTONS":
                for index, button in enumerate(component.get("buttons") or []):
                    button_type = {
                        "QUICK_REPLY": "quick_reply", "URL": "url", "PHONE_NUMBER": "phone_number",
                    }.get((button.get("type") or "").upper())
                    if not button_type:
                        continue
                    buttons.append({
                        "sequence": index,
                        "button_type": button_type,
                        "text": button.get("text") or "",
                        "website_url": button.get("url") or False,
                        "url_type": "dynamic" if "{{" in (button.get("url") or "") else "static",
                        "call_number": button.get("phone_number") or False,
                    })
        if "body" not in vals:
            vals["body"] = ""
        vals["button_ids"] = [Command.clear()] + [Command.create(b) for b in buttons]
        return vals

    # ------------------------------------------------------------------
    # Sending a template from a record (SPEC.md §8.1, §29, R5, R25)
    # ------------------------------------------------------------------

    def _wa_check_sendable(self, user):
        self.ensure_one()
        if self.status != "APPROVED":
            raise UserError(self.env._("The template %s is not approved by Meta.", self.name))
        if self.user_ids and user not in self.user_ids:
            raise UserError(self.env._("You are not allowed to send the template %s.", self.name))

    def _wa_recipient_partner(self, record):
        """The contact to send to, from the template's Phone Field (R25)."""
        self.ensure_one()
        if record._name == "res.partner":
            return record
        value = record
        for part in (self.phone_field or "partner_id").split("."):
            value = value[part] if part in value._fields else False
            if not value:
                break
        if isinstance(value, models.BaseModel) and value._name == "res.partner":
            return value[:1]
        if isinstance(value, str) and value:
            phone = self.env["res.partner"]._wa_normalize_phone(value)
            partner = self.env["res.partner"].sudo().search([("phone_sanitized", "=", phone)], limit=1)
            return partner or self.env["res.partner"].sudo().create({
                "name": record.display_name, "phone": phone,
            })
        return self.env["res.partner"]

    def _wa_variable_value(self, variable, record, user, free_values=None):
        if variable.field_type == "free_text":
            return (free_values or {}).get(variable.id) or variable.demo_value
        if variable.field_type == "user_name":
            return user.name
        if variable.field_type == "portal_url":
            if hasattr(record, "get_portal_url"):
                return f"{record.get_base_url()}{record.get_portal_url()}"
            return record.get_base_url()
        value = record
        for part in (variable.field_name or "").split("."):
            if not part or not isinstance(value, models.BaseModel) or part not in value._fields:
                return ""
            value = value[:1][part]
        if isinstance(value, models.BaseModel):
            return value[:1].display_name or ""
        return "" if value is False or value is None else str(value)

    def _wa_render(self, record, user, free_values=None):
        """Values of the placeholders, per line: {"header": {1: ..}, "body": {1: .., 2: ..}}."""
        self.ensure_one()
        values = {"header": {}, "body": {}, "button": {}}
        for variable in self.variable_ids:
            values[variable.line_type][variable.placeholder_index] = self._wa_variable_value(
                variable, record, user, free_values,
            )
        return values

    @api.model
    def _wa_fill(self, text, values):
        for index, value in values.items():
            text = text.replace("{{%s}}" % index, value)
        return text

    def _wa_payload(self, values, api_client):
        """Meta's template object (R25), built when sending: a media header is uploaded then.

        ``values`` is what :meth:`_wa_render` returned, possibly after a JSON
        round trip (string keys).
        """
        self.ensure_one()

        def ordered(line):
            items = (values.get(line) or {}).items()
            return [value for _index, value in sorted(items, key=lambda item: int(item[0]))]

        components = []
        if self.header_type == "text" and ordered("header"):
            components.append({"type": "header", "parameters": [
                {"type": "text", "text": text} for text in ordered("header")
            ]})
        elif self.header_type in ("image", "video", "document") and self.header_attachment_id:
            attachment = self.header_attachment_id.sudo()
            media = {"id": api_client.upload_media(attachment.name, attachment.mimetype, attachment.raw)}
            if self.header_type == "document":
                media["filename"] = self.header_attachment_id.name
            components.append({"type": "header", "parameters": [{"type": self.header_type, self.header_type: media}]})
        if ordered("body"):
            components.append({"type": "body", "parameters": [
                {"type": "text", "text": text} for text in ordered("body")
            ]})
        button_values = {int(k): v for k, v in (values.get("button") or {}).items()}
        for index, button in enumerate(self.button_ids):
            if button.button_type == "url" and button.url_type in ("dynamic", "tracked") and button_values:
                components.append({
                    "type": "button", "sub_type": "url", "index": str(index),
                    "parameters": [{"type": "text", "text": button_values.get(index + 1, "")}],
                })
        # "deterministic" is the only language policy Meta accepts.
        payload = {
            "name": self.template_name,
            "language": {"policy": "deterministic", "code": self.language_code},
        }
        if components:
            payload["components"] = components
        return payload

    def _wa_send_to_record(self, record, user=None, free_values=None):
        """Send this template for ``record``; return the WhatsApp message queued.

        The message is posted once, in the customer's WhatsApp conversation
        (the canonical copy); the record's chatter gets a note linking to it
        (SPEC.md §30).
        """
        self.ensure_one()
        user = user or self.env.user
        self._wa_check_sendable(user)
        partner = self._wa_recipient_partner(record)
        if not partner:
            raise UserError(self.env._("No contact to send the template to on %s.", record.display_name))
        account = self.account_id
        Channel = self.env["discuss.channel"].sudo()
        channel = Channel._wa_conversation_for_partner(account, partner, user)
        if account.routing_mode == "lead":
            channel._wa_assign_sender(user, record)  # D2 / D6, Lead Routing
        values = self._wa_render(record, user, free_values)
        body_text = self._wa_fill(self.body or "", values["body"])
        header = self._wa_fill(self.header_text or "", values["header"]) if self.header_type == "text" else ""
        shown = "\n\n".join(filter(None, [header and f"*{header}*", body_text, self.footer]))
        message = channel.with_user(user).with_context(wa_skip_send=True).message_post(
            body=plaintext2html(shown), message_type="whatsapp_message",
            subtype_xmlid="mail.mt_comment",
        )
        wa_message = self.env["whatsapp_connector.message"].sudo().create({
            "account_id": account.id,
            "channel_id": channel.id,
            "mail_message_id": message.id,
            "direction": "outbound",
            "message_type": "template",
            "template_id": self.id,
            "status": "queued",
            "body": shown,
            "sender": account.phone_number or account.phone_number_id,
            "recipient": channel.wa_customer_phone or channel.wa_bsuid,
            "payload": values,  # Meta's payload is built when sending (header upload)
        })
        channel._wa_after_user_message(user.partner_id)
        if record._name != "discuss.channel" and hasattr(record, "message_post"):
            record.message_post(
                body=Markup("%s %s") % (
                    self.env._("WhatsApp template %s sent in", self.name), channel._get_html_link(),
                ),
                message_type="notification", subtype_xmlid="mail.mt_note",
            )
        wa_message._wa_trigger_send()
        return wa_message


class WhatsappTemplateButton(models.Model):
    _name = "whatsapp_connector.template.button"
    _description = "WhatsApp Template Button"
    _order = "sequence, id"

    template_id = fields.Many2one("whatsapp_connector.template", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    button_type = fields.Selection(
        [("quick_reply", "Quick Reply"), ("url", "Visit Website"), ("phone_number", "Call Number")],
        "Type", required=True, default="quick_reply",
    )
    text = fields.Char("Button Text", required=True)
    url_type = fields.Selection(
        [("static", "Static"), ("dynamic", "Dynamic"), ("tracked", "Tracked")], default="static",
    )
    website_url = fields.Char("Website URL")
    call_number = fields.Char()


class WhatsappTemplateVariable(models.Model):
    _name = "whatsapp_connector.template.variable"
    _description = "WhatsApp Template Variable"
    _order = "line_type, placeholder_index, id"

    template_id = fields.Many2one("whatsapp_connector.template", required=True, ondelete="cascade")
    line_type = fields.Selection(
        [("header", "Header"), ("body", "Body"), ("button", "Button")],
        required=True, default="body",
    )
    placeholder_index = fields.Integer("Placeholder", required=True, default=1, help="{{1}} is 1.")
    field_type = fields.Selection(
        [("free_text", "Free Text"), ("field", "Field of Model"), ("portal_url", "Portal link"),
         ("user_name", "Sender's Name")],
        "Type", required=True, default="field",
    )
    field_name = fields.Char("Field", help="Dotted path from the template's model, e.g. partner_id.name.")
    demo_value = fields.Char("Sample Value", required=True, default="Sample")
