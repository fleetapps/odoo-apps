# -*- coding: utf-8 -*-
"""The queue learns four more kinds of document.

None of them takes a sales invoice number. Stock movements and purchases
carry the trader's own running numbers (``kra_sequence_no``), drawn once and
kept across retries like an invoice number is. None of them is signed, either:
KRA answers "000" and there is no receipt, so a success is recorded as "sent".
"""

from odoo import fields, models

from odoo.addons.x_ke_etims.backends import EtimsError, Receipt


class KeEtimsDocument(models.Model):
    _inherit = "ke.etims.document"

    kind = fields.Selection(
        selection_add=[
            ("stock_io", "Stock Movement"),
            ("stock_master", "Stock Level"),
            ("purchase", "Purchase Confirmation"),
            ("import_update", "Import Approval"),
        ],
        ondelete={"stock_io": "cascade", "stock_master": "cascade",
                  "purchase": "cascade", "import_update": "cascade"})
    kra_sequence_no = fields.Integer(
        string="SAR / Purchase No.", readonly=True, copy=False,
        help="The trader's own running number KRA files a stock movement or a "
             "confirmed purchase under. Drawn once, kept across retries.")

    def _ke_etims_dispatch(self, adapter, data):
        if self.kind == "stock_io":
            try:
                adapter.report_stock_io(data)
            except EtimsError as exc:
                if exc.code != "handled_by_provider":
                    raise
                # DigiTax moves its own stock with the sale or purchase.
                return Receipt(status="signed", provider_ref=str(exc))
            return Receipt(status="signed")
        if self.kind == "stock_master":
            for master in data:
                adapter.save_stock_master(master)
            return Receipt(status="signed")
        if self.kind == "purchase":
            adapter.confirm_purchase(data)
            return Receipt(status="signed")
        if self.kind == "import_update":
            adapter.update_import(data)
            return Receipt(status="signed")
        return super()._ke_etims_dispatch(adapter, data)

    def _ke_etims_requeue(self):
        """Send a "latest value" document (a stock level) again."""
        self.sudo().write({"state": "to_send", "attempts": 0, "next_try": False,
                           "last_error": False})
