from odoo import _, fields, models


class StockBookSale(models.Model):
    _inherit = "odin.stock.sale"

    photo_ids = fields.One2many("odin.stock.photo", "sale_id", string="Invoice photos", readonly=True)

    def _make_documents(self):
        result = super()._make_documents()
        # A sale saved for posting and posted later: its photos reach the invoice now.
        self._photos_to_invoice()
        return result

    def _photos_to_invoice(self):
        """A copy of the photos in the invoice's chatter, beside it in
        Accounting: once per invoice. A free sale has no invoice, and its
        photos stay with the sale."""
        for sale in self:
            invoice = sale.sudo().invoice_id
            photos = sale.sudo().photo_ids.filtered(lambda p: p.invoice_id != invoice)
            if not invoice or not photos:
                continue
            copies = self.env["ir.attachment"].sudo().create([{
                "name": page.name, "raw": page.raw, "mimetype": page.mimetype,
                "res_model": "account.move", "res_id": invoice.id,
            } for page in photos._pages()])
            invoice.message_post(
                body=_("Photo of the invoice as delivered, uploaded in the Stock Book's Add sale."),
                attachment_ids=copies.ids, subtype_xmlid="mail.mt_note")
            photos.write({"invoice_id": invoice.id})
