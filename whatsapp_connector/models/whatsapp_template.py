import re

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import plaintext2html

from odoo.addons.whatsapp_connector.tools.meta_api import MetaApiError

MEDIA_HEADER_ICONS = {
    "image": "fa-picture-o", "video": "fa-video-camera", "document": "fa-file-text-o",
    "location": "fa-map-marker",
}

# Positional placeholders, {{1}}, {{2}}, ... (the only kind this module fills).
PLACEHOLDER = re.compile(r"\{\{(\d+)\}\}")

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
    preview_html = fields.Html(
        "Preview", compute="_compute_preview_html", sanitize=True,
        help="How the message looks in WhatsApp, with the sample values.",
    )
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

    @api.depends("header_type", "header_text", "body", "footer", "button_ids.text",
                 "variable_ids.demo_value", "variable_ids.placeholder_index", "variable_ids.line_type")
    def _compute_preview_html(self):
        for template in self:
            values = {"header": {}, "body": {}}
            for variable in template.variable_ids.filtered(lambda v: v.line_type in values):
                values[variable.line_type][str(variable.placeholder_index)] = variable.demo_value or ""
            template.preview_html = template._wa_preview_html(values)

    def _wa_preview_html(self, values):
        """The message as a WhatsApp bubble: header, body, footer, then its buttons."""
        self.ensure_one()

        def lines(text):
            return Markup("<br/>").join(escape(line) for line in (text or "").split("\n"))

        parts = []
        if self.header_type == "text" and self.header_text:
            header = self._wa_fill(self.header_text, values.get("header") or {})
            parts.append(Markup('<div class="mb-1"><strong>%s</strong></div>') % lines(header))
        elif self.header_type in MEDIA_HEADER_ICONS:
            label = dict(self._fields["header_type"]._description_selection(self.env))[self.header_type]
            parts.append(Markup('<div class="text-muted small mb-1"><i class="fa %s"></i> %s</div>') % (
                MEDIA_HEADER_ICONS[self.header_type], label,
            ))
        parts.append(Markup("<div>%s</div>") % lines(self._wa_fill(self.body or "", values.get("body") or {})))
        if self.footer:
            parts.append(Markup('<div class="text-muted small mt-1">%s</div>') % lines(self.footer))
        buttons = Markup("").join(
            Markup('<div class="o-whatsapp-bubble-button">%s</div>') % button.text
            for button in self._wa_ordered_buttons()
        )
        return Markup('<div class="o-whatsapp-preview"><div class="o-whatsapp-bubble">%s</div>%s</div>') % (
            Markup("").join(parts), buttons,
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
            template = by_id.get(data.get("id")) or by_name.get((name, language))
            if not template:
                template = self.create({
                    "name": name.replace("_", " ").capitalize(),
                    "template_name": name,
                    "language_code": language,
                    "account_id": account.id,
                    # usable at once from contacts; "Applies to" can be changed after
                    "model_id": self.env["ir.model"]._get_id("res.partner"),
                })
            template._wa_update_from_meta(data)
            count += 1
        return count

    def _wa_update_from_meta(self, data):
        """Apply Meta's template object (list item or GET /{TEMPLATE_ID})."""
        self.ensure_one()
        vals = {
            "meta_template_id": data.get("id") or self.meta_template_id or False,
            "status": data.get("status") or False,
            "body": "",
        }
        category = (data.get("category") or "").lower()
        if category in ("marketing", "utility", "authentication"):
            vals["category"] = category
        quality = data.get("quality_score")
        if quality:
            vals["quality"] = quality.get("score") if isinstance(quality, dict) else str(quality)
        buttons, examples = [], {"header": [], "body": [], "button": {}}
        for component in data.get("components") or []:
            kind = (component.get("type") or "").upper()
            example = component.get("example") or {}
            if kind == "BODY":
                vals["body"] = component.get("text") or ""
                examples["body"] = (example.get("body_text") or [[]])[0]
            elif kind == "FOOTER":
                vals["footer"] = component.get("text") or False
            elif kind == "HEADER":
                header_format = (component.get("format") or "TEXT").lower()
                if header_format in ("text", "image", "video", "document", "location"):
                    vals["header_type"] = header_format
                vals["header_text"] = component.get("text") or False
                examples["header"] = example.get("header_text") or []
            elif kind == "BUTTONS":
                for button in component.get("buttons") or []:
                    button_type = {
                        "QUICK_REPLY": "quick_reply", "URL": "url", "PHONE_NUMBER": "phone_number",
                    }.get((button.get("type") or "").upper())
                    if not button_type:
                        continue
                    url = button.get("url") or ""
                    if PLACEHOLDER.search(url):
                        examples["button"][len(buttons)] = (button.get("example") or [""])[0]
                    buttons.append({
                        "button_type": button_type,
                        "text": button.get("text") or "",
                        "website_url": url or False,
                        "url_type": "dynamic" if PLACEHOLDER.search(url) else "static",
                        "call_number": button.get("phone_number") or False,
                    })
        if "header_type" not in vals:
            vals.update({"header_type": "none", "header_text": False})
        self.write(vals)
        self._wa_sync_buttons(buttons)
        self._wa_sync_variables(examples)

    def _wa_sync_buttons(self, buttons):
        """Update the buttons in place, by position, so variables linked to them survive a sync."""
        current = self.button_ids.sorted(lambda b: (b.sequence, b.id))
        for position, values in enumerate(buttons):
            values["sequence"] = position
            if position < len(current):
                current[position].write(values)
            else:
                self.env["whatsapp_connector.template.button"].create({**values, "template_id": self.id})
        current[len(buttons):].unlink()

    def _wa_sync_variables(self, examples):
        """One variable per placeholder; Meta's example values become the sample values.

        Variables already configured are kept; those of removed placeholders go.
        """
        wanted = {}
        for line, text in (("header", self.header_text if self.header_type == "text" else ""),
                           ("body", self.body)):
            for index in sorted({int(n) for n in PLACEHOLDER.findall(text or "")}):
                sample = examples[line][index - 1] if index - 1 < len(examples[line]) else ""
                wanted[line, index, False] = sample
        for position, button in enumerate(self._wa_ordered_buttons()):
            if button.button_type == "url" and PLACEHOLDER.search(button.website_url or ""):
                wanted["button", 1, button.id] = examples["button"].get(position, "")
        existing = {(v.line_type, v.placeholder_index, v.button_id.id): v for v in self.variable_ids}
        (self.variable_ids - self.variable_ids.browse(
            [v.id for key, v in existing.items() if key in wanted],
        )).unlink()
        self.env["whatsapp_connector.template.variable"].create([
            {
                "template_id": self.id, "line_type": line, "placeholder_index": index,
                "button_id": button_id, "field_type": "free_text", "demo_value": sample or "Sample",
            }
            for (line, index, button_id), sample in wanted.items()
            if (line, index, button_id) not in existing
        ])

    def _wa_ordered_buttons(self):
        return self.button_ids.sorted(lambda b: (b.sequence, b.id))

    def action_sync_template(self):
        """Sync this template from Meta (R25: "Sync Template")."""
        self.ensure_one()
        if not self.meta_template_id:
            raise UserError(self.env._("This template has not been submitted to Meta yet."))
        try:
            data = self.account_id._api().get_template(self.meta_template_id)
        except MetaApiError as e:
            raise UserError(self.env._("Could not read the template from Meta: %s", e)) from None
        self._wa_update_from_meta(data)
        return True

    # ------------------------------------------------------------------
    # Submit for approval (R25)
    # ------------------------------------------------------------------

    def action_submit_template(self):
        """Send the template to Meta for review: created, or edited once it exists there."""
        self.ensure_one()
        components = self._wa_meta_components()
        try:
            api_client = self.account_id._api()
            if self.meta_template_id:
                api_client.edit_template(self.meta_template_id, {
                    "category": self.category.upper(), "components": components,
                })
                # the edit answers only success: read the template back for its status
                self._wa_update_from_meta(api_client.get_template(self.meta_template_id))
            else:
                result = api_client.submit_template({
                    "name": self.template_name,
                    "language": self.language_code,
                    "category": self.category.upper(),
                    "components": components,
                })
                vals = {"meta_template_id": result.get("id"), "status": result.get("status") or "PENDING"}
                category = (result.get("category") or "").lower()
                if category in ("marketing", "utility", "authentication"):
                    vals["category"] = category  # Meta may recategorize
                self.write(vals)
        except MetaApiError as e:
            raise UserError(self.env._("Meta refused the template: %s", e)) from None
        return True

    def _wa_meta_components(self):
        """The template in Meta's creation format, with the sample values as examples."""
        self.ensure_one()
        tr = self.env._
        if not (self.body or "").strip():
            raise UserError(tr("The template needs a body."))
        if self.header_type in ("image", "video", "document"):
            raise UserError(tr(
                "Templates with an image, video or document header must be created in WhatsApp "
                "Manager, then synced: submitting media samples from Odoo is not supported yet.",
            ))
        samples = {(v.line_type, v.placeholder_index, v.button_id.id): v.demo_value for v in self.variable_ids}

        def examples(line, text):
            indexes = sorted({int(n) for n in PLACEHOLDER.findall(text or "")})
            missing = [i for i in indexes if (line, i, False) not in samples]
            if missing:
                raise UserError(tr(
                    "Add a variable with a sample value for {{%(index)s}} of the %(line)s.",
                    index=missing[0], line=line,
                ))
            return [samples[line, i, False] for i in indexes]

        components = []
        if self.header_type == "text":
            header = {"type": "HEADER", "format": "TEXT", "text": self.header_text or ""}
            if header_examples := examples("header", self.header_text):
                header["example"] = {"header_text": header_examples}
            components.append(header)
        elif self.header_type == "location":
            components.append({"type": "HEADER", "format": "LOCATION"})
        body = {"type": "BODY", "text": self.body}
        if body_examples := examples("body", self.body):
            body["example"] = {"body_text": [body_examples]}
        components.append(body)
        if self.footer:
            components.append({"type": "FOOTER", "text": self.footer})
        buttons = []
        for button in self._wa_ordered_buttons():
            if button.button_type == "quick_reply":
                buttons.append({"type": "QUICK_REPLY", "text": button.text})
            elif button.button_type == "phone_number":
                buttons.append({"type": "PHONE_NUMBER", "text": button.text, "phone_number": button.call_number})
            else:
                values = {"type": "URL", "text": button.text, "url": button.website_url}
                if PLACEHOLDER.search(button.website_url or ""):
                    if ("button", 1, button.id) not in samples:
                        raise UserError(tr(
                            "Add a variable with a sample value for {{1}} of the button %s.", button.text,
                        ))
                    values["example"] = [samples["button", 1, button.id]]
                buttons.append(values)
        if buttons:
            components.append({"type": "BUTTONS", "buttons": buttons})
        return components

    # ------------------------------------------------------------------
    # Sending a template from a record (SPEC.md §8.1, §29, R5, R25)
    # ------------------------------------------------------------------

    @api.model
    def _wa_available(self, model, user=None):
        """Approved templates the user may send from records of ``model`` (R10, R25)."""
        user = user or self.env.user
        if not model or not user.has_group("whatsapp_connector.group_whatsapp_user"):
            return self.browse()
        return self.search([
            ("model", "=", model), ("status", "=", "APPROVED"), ("account_id.active", "=", True),
            "|", ("user_ids", "=", False), ("user_ids", "in", user.ids),
        ])

    def _wa_check_sendable(self, user):
        self.ensure_one()
        tr = self.env._
        if self.status != "APPROVED":
            raise UserError(tr("The template %s is not approved by Meta.", self.name))
        if self.user_ids and user not in self.user_ids:
            raise UserError(tr("You are not allowed to send the template %s.", self.name))
        if self.header_type in ("image", "video", "document") and not self.header_attachment_id:
            raise UserError(tr("The template %s needs its header file before it can be sent.", self.name))
        if self.header_type == "location":
            raise UserError(tr("Templates with a location header cannot be sent from Odoo yet."))
        defined = {(v.line_type, v.placeholder_index, v.button_id.id) for v in self.variable_ids}
        needed = [("body", int(n), False) for n in PLACEHOLDER.findall(self.body or "")]
        if self.header_type == "text":
            needed += [("header", int(n), False) for n in PLACEHOLDER.findall(self.header_text or "")]
        needed += [
            ("button", 1, button.id) for button in self.button_ids
            if button.button_type == "url" and PLACEHOLDER.search(button.website_url or "")
        ]
        if missing := [key for key in needed if key not in defined]:
            raise UserError(tr(
                "The template %(template)s has no variable for {{%(index)s}} of its %(line)s.",
                template=self.name, index=missing[0][1], line=missing[0][0],
            ))

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
        """Values of the placeholders, with string keys as they are stored (JSON).

        ``{"header": {"1": ..}, "body": {"1": .., "2": ..}, "button": {"<position>": ..}}``;
        a button's key is its position, which is Meta's button ``index``.
        """
        self.ensure_one()
        values = {"header": {}, "body": {}, "button": {}}
        positions = {button: str(i) for i, button in enumerate(self._wa_ordered_buttons())}
        for variable in self.variable_ids:
            value = self._wa_variable_value(variable, record, user, free_values)
            if variable.line_type == "button":
                if variable.button_id in positions:
                    values["button"][positions[variable.button_id]] = value
            else:
                values[variable.line_type][str(variable.placeholder_index)] = value
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
        button_values = values.get("button") or {}
        for index, button in enumerate(self._wa_ordered_buttons()):
            if str(index) in button_values:
                components.append({
                    "type": "button", "sub_type": "url", "index": str(index),
                    "parameters": [{"type": "text", "text": button_values[str(index)]}],
                })
        # "deterministic" is the only language policy Meta accepts.
        payload = {
            "name": self.template_name,
            "language": {"policy": "deterministic", "code": self.language_code},
        }
        if components:
            payload["components"] = components
        return payload

    def _wa_send_to_record(self, record, user=None, free_values=None, channel=None):
        """Send this template for ``record``; return the WhatsApp message queued.

        The message is posted once, in the customer's WhatsApp conversation
        (the canonical copy); the record's chatter gets a note linking to it
        (SPEC.md §30). ``channel`` forces the conversation (sent from Discuss).
        """
        self.ensure_one()
        user = user or self.env.user
        self._wa_check_sendable(user)
        account = self.account_id
        if channel:
            if channel.wa_account_id != account:
                raise UserError(self.env._("This template belongs to another WhatsApp account."))
            channel = channel.sudo()
            if user.partner_id not in channel.channel_member_ids.partner_id:
                channel._add_members(partners=user.partner_id, post_joined_message=False)
        else:
            partner = self._wa_recipient_partner(record)
            if not partner:
                raise UserError(self.env._("No contact to send the template to on %s.", record.display_name))
            channel = self.env["discuss.channel"].sudo()._wa_conversation_for_partner(account, partner, user)
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
        message._wa_notify_delivery()
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
        help="Dynamic: the URL ends with {{1}}, filled by the button's variable. Tracked: click "
        "tracking is not implemented yet; such a button is sent like a dynamic one.",
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
    placeholder_index = fields.Integer(
        "Placeholder", required=True, default=1, help="{{1}} is 1. A button has a single {{1}}.",
    )
    button_id = fields.Many2one(
        "whatsapp_connector.template.button", "Button", ondelete="cascade",
        domain="[('template_id', '=', template_id), ('button_type', '=', 'url')]",
    )
    field_type = fields.Selection(
        [("free_text", "Free Text"), ("field", "Field of Model"), ("portal_url", "Portal link"),
         ("user_name", "Sender's Name")],
        "Type", required=True, default="field",
    )
    field_name = fields.Char("Field", help="Dotted path from the template's model, e.g. partner_id.name.")
    model = fields.Char(related="template_id.model", string="Model")  # for the field picker
    demo_value = fields.Char("Sample Value", required=True, default="Sample")

    @api.constrains("line_type", "button_id", "placeholder_index")
    def _check_line(self):
        for variable in self:
            if variable.line_type == "button" and not variable.button_id:
                raise ValidationError(self.env._("A button variable must say which button it fills."))
            if variable.placeholder_index < 1:
                raise ValidationError(self.env._("Placeholders start at {{1}}."))
