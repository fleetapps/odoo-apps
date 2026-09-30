from odoo import Command, api, fields, models

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


class WhatsappTemplateSync(models.Model):
    _inherit = "whatsapp_connector.template"

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
