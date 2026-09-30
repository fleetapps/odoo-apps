from odoo import api, fields, models


class WhatsappWebhookEvent(models.Model):
    """One webhook POST from Meta, stored before anything else happens.

    Meta: "There are no APIs for fetching historical webhook data, so capture
    and store webhook payloads accordingly." (SPEC.md §33, R35). The endpoint
    stores the body, answers 200 and a cron processes it (R27).
    """

    _name = "whatsapp_connector.webhook.event"
    _description = "WhatsApp Webhook Event"
    _order = "id desc"
    _rec_name = "received_at"

    received_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    raw_body = fields.Text(required=True, help="The request body exactly as Meta sent it.")
    state = fields.Selection(
        [("new", "New"), ("processed", "Processed"), ("error", "Error")],
        default="new", required=True, index=True,
    )
    error = fields.Text()
    processed_at = fields.Datetime()
    attempts = fields.Integer(default=0)

    @api.autovacuum
    def _gc_processed_events(self):
        days = int(self.env["ir.config_parameter"].sudo().get_param(
            "whatsapp_connector.webhook_retention_days", "0"
        ) or 0)
        if days <= 0:  # 0: keep every event (the default)
            return
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        self.search([("state", "=", "processed"), ("received_at", "<", limit)]).unlink()
