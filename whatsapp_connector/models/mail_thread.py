from odoo import models


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _thread_to_store(self, store, fields, *, request_list=None):
        """Tell the chatter whether its WhatsApp button applies (R10)."""
        super()._thread_to_store(store, fields, request_list=request_list)
        if not request_list or "wa_can_send" not in request_list:
            return
        can_send = bool(self.env["whatsapp_connector.template"]._wa_available(self._name)[:1])
        for thread in self:
            store.add(thread, {"wa_can_send": can_send}, as_thread=True)
