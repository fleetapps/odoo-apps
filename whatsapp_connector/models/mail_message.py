from odoo import fields, models

from odoo.addons.mail.tools.discuss import Store
from odoo.addons.whatsapp_connector.models.whatsapp_message import STATUS_RANK

STATUS_BY_RANK = {rank: status for status, rank in STATUS_RANK.items()}


class MailMessage(models.Model):
    _inherit = "mail.message"

    # The key Odoo Enterprise's WhatsApp app uses, handled by Odoo 19 Community
    # core for notifications (SPEC.md §12.1, R9). On uninstall the messages
    # become comments, so the history is kept (R33).
    message_type = fields.Selection(
        selection_add=[("whatsapp_message", "WhatsApp")],
        ondelete={"whatsapp_message": "set default"},
    )
    wa_message_ids = fields.One2many("whatsapp_connector.message", "mail_message_id")

    def _to_store_defaults(self, target):
        """The WhatsApp delivery status shown on sent messages (SPEC.md §35)."""
        def is_whatsapp(message):
            return message.message_type == "whatsapp_message"

        return super()._to_store_defaults(target) + [
            Store.Attr("whatsappStatus", lambda m: m._wa_delivery()[0], predicate=is_whatsapp),
            Store.Attr("whatsappError", lambda m: m._wa_delivery()[1], predicate=is_whatsapp),
        ]

    def _wa_delivery(self):
        """(status, error) of what was sent to the customer for this message, or (False, False).

        A message can be several WhatsApp messages (files and text): a failure
        shows first, otherwise the least advanced status.
        """
        self.ensure_one()
        # sudo: the status of a message the user can read
        sent = self.sudo().wa_message_ids.filtered(lambda m: m.direction == "outbound")
        if not sent:
            return False, False
        if problems := sent.filtered(lambda m: m.status in ("failed", "dropped")):
            problem = problems[0]
            return problem.status, problem.error_title or problem.error_message or False
        return STATUS_BY_RANK[min(STATUS_RANK.get(m.status, 0) for m in sent)], False

    def _wa_notify_delivery(self):
        """Push the delivery status to the conversation's members."""
        for message in self.filtered(lambda m: m.model == "discuss.channel" and m.res_id):
            channel = self.env["discuss.channel"].sudo().browse(message.res_id)
            status, error = message._wa_delivery()
            Store(bus_channel=channel).add(
                message, {"whatsappStatus": status, "whatsappError": error},
            ).bus_send()
