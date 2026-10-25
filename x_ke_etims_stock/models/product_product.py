# -*- coding: utf-8 -*-
"""A product's stock level on a device: KRA's stock master.

A level is a latest value, so a product has one stock-master document per
device, sent again each time its stock moves; the quantity is read when the
document is sent, not when it was queued, so a burst of movements ends in
one up-to-date figure rather than a string of stale ones.
"""

from odoo import _, models

from odoo.addons.x_ke_etims.backends import StockMaster


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _ke_etims_enqueue_stock_master(self, device, products):
        Document = self.env["ke.etims.document"].sudo()
        for product in products:
            # One document per product and device, sent again each time.
            existing = Document.search([
                ("res_model", "=", "product.product"), ("res_id", "=", product.id),
                ("kind", "=", "stock_master"), ("device_id", "=", device.id),
                ("state", "!=", "cancelled")], limit=1)
            if existing:
                if existing.state != "sending":
                    existing._ke_etims_requeue()
                continue
            Document.create({
                "res_model": "product.product", "res_id": product.id,
                "kind": "stock_master", "device_id": device.id,
                "company_id": device.company_id.id,
            })

    # ------------------------------------------------- the eTIMS source API

    def _ke_etims_device(self):
        return self.env["ke.etims.device"]

    def _ke_etims_validate(self, document):
        self.ensure_one()
        template = self.product_tmpl_id
        if not template._ke_etims_registration(document.device_id):
            return [_("%s is not registered with eTIMS on this device.", template.display_name)]
        return []

    def _ke_etims_prepare(self, document):
        self.ensure_one()
        device = document.device_id
        registration = self.product_tmpl_id._ke_etims_registration(device)
        on_hand = self.with_company(device.company_id).qty_available
        return [StockMaster(
            item_code=registration.item_code,
            remaining_quantity=round(on_hand, 2),
            operator=device._ke_etims_operator(),
            provider_item_id=registration.provider_item_id or None,
        )]

    def _ke_etims_on_signed(self, document):
        return

    def _ke_etims_on_failed(self, document):
        document._ke_etims_schedule_activity()
