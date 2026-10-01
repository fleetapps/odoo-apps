from ast import literal_eval

from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class WhatsappComposer(models.TransientModel):
    """"Send WhatsApp Message": a template sent to one or several records (SPEC.md §13.1, R10, R25).

    Opened from a record's chatter, a list's selection, or a WhatsApp
    conversation in Discuss (its customer's contact, in that conversation).
    """

    _name = "whatsapp_connector.composer"
    _description = "Send WhatsApp Message"

    res_model = fields.Char("Document Model", required=True)
    res_ids = fields.Char("Document IDs", required=True, help="A list of IDs, as sms.composer keeps them.")
    channel_id = fields.Many2one("discuss.channel", "Conversation", readonly=True)
    record_count = fields.Integer(compute="_compute_record_count")
    available_template_ids = fields.Many2many(
        "whatsapp_connector.template", compute="_compute_available_template_ids",
    )
    template_id = fields.Many2one(
        "whatsapp_connector.template", "Template", domain="[('id', 'in', available_template_ids)]",
    )
    free_text_ids = fields.One2many(
        "whatsapp_connector.composer.free.text", "composer_id", "Free Text Values",
        compute="_compute_free_text_ids", store=True, readonly=False,
    )
    preview = fields.Text("Preview Text", compute="_compute_preview")
    preview_html = fields.Html("Preview", compute="_compute_preview", sanitize=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        context = self.env.context
        model = context.get("active_model")
        ids = context.get("active_ids") or ([context["active_id"]] if context.get("active_id") else [])
        if model == "discuss.channel" and ids:
            channel = self.env["discuss.channel"].browse(ids[0])
            channel.check_access("read")
            if channel.channel_type != "whatsapp" or not channel.wa_partner_id:
                raise UserError(self.env._("Templates are sent from WhatsApp conversations."))
            # a conversation's templates are those of its customer's contact
            values.update({"res_model": "res.partner", "res_ids": str(channel.wa_partner_id.ids),
                           "channel_id": channel.id})
        elif model and ids:
            values.update({"res_model": model, "res_ids": str(list(ids))})
        return values

    @api.depends("res_ids")
    def _compute_record_count(self):
        for composer in self:
            composer.record_count = len(composer._ids_list())

    @api.depends("res_model", "channel_id")
    def _compute_available_template_ids(self):
        Template = self.env["whatsapp_connector.template"]
        for composer in self:
            templates = Template._wa_available(composer.res_model)
            if composer.channel_id:
                templates = templates.filtered(lambda t: t.account_id == composer.channel_id.wa_account_id)
            composer.available_template_ids = templates

    @api.depends("template_id")
    def _compute_free_text_ids(self):
        for composer in self:
            variables = composer.template_id.variable_ids.filtered(lambda v: v.field_type == "free_text")
            composer.free_text_ids = [Command.clear()] + [
                Command.create({"variable_id": v.id, "value": v.demo_value}) for v in variables
            ]

    @api.depends("template_id", "free_text_ids.value")
    def _compute_preview(self):
        for composer in self:
            template = composer.template_id
            records = composer._records()
            if not template or not records:
                composer.preview = composer.preview_html = False
                continue
            # the first record's values, as the customer will see them
            values = template._wa_render(records[0], self.env.user, composer._free_values())
            header = template._wa_fill(template.header_text or "", values["header"]) \
                if template.header_type == "text" else ""
            body = template._wa_fill(template.body or "", values["body"])
            composer.preview = "\n\n".join(filter(None, [header, body, template.footer]))
            composer.preview_html = template._wa_preview_html(values)

    def _ids_list(self):
        try:
            ids = literal_eval(self.res_ids or "[]")
        except (ValueError, SyntaxError):
            return []
        return [i for i in ids if isinstance(i, int)] if isinstance(ids, (list, tuple)) else []

    def _records(self):
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            return self.env["res.partner"].browse()
        return self.env[self.res_model].browse(self._ids_list()).exists()

    def _free_values(self):
        return {line.variable_id.id: line.value or "" for line in self.free_text_ids}

    def action_send(self):
        """Send to every record; with several records, those that cannot be reached are reported."""
        self.ensure_one()
        if not self.template_id:
            raise UserError(self.env._("Choose a template."))
        if self.template_id not in self.available_template_ids:
            raise UserError(self.env._("This template cannot be sent from here."))
        records = self._records()
        records.check_access("read")
        free_values = self._free_values()
        if len(records) == 1:
            self.template_id._wa_send_to_record(records, free_values=free_values, channel=self.channel_id)
            return {"type": "ir.actions.act_window_close"}
        sent, failed = 0, []
        for record in records:
            try:
                with self.env.cr.savepoint():
                    self.template_id._wa_send_to_record(record, free_values=free_values)
                sent += 1
            except UserError as e:
                failed.append(f"{record.display_name}: {e}")
        message = self.env._("%(sent)s of %(total)s WhatsApp messages queued.", sent=sent, total=len(records))
        if failed:
            message += "\n" + "\n".join(failed[:10])
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": message,
                "type": "warning" if failed else "success",
                "sticky": bool(failed),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class WhatsappComposerFreeText(models.TransientModel):
    _name = "whatsapp_connector.composer.free.text"
    _description = "WhatsApp Message Free Text Value"

    composer_id = fields.Many2one("whatsapp_connector.composer", required=True, ondelete="cascade")
    variable_id = fields.Many2one("whatsapp_connector.template.variable", required=True, ondelete="cascade")
    placeholder = fields.Char(compute="_compute_placeholder")
    value = fields.Char()

    @api.depends("variable_id")
    def _compute_placeholder(self):
        lines = dict(self.env["whatsapp_connector.template.variable"]._fields["line_type"]._description_selection(self.env))
        for line in self:
            variable = line.variable_id
            where = variable.button_id.text if variable.line_type == "button" else lines.get(variable.line_type)
            line.placeholder = f"{where} {{{{{variable.placeholder_index}}}}}"
