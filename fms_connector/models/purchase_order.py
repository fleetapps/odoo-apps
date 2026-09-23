# -*- coding: utf-8 -*-
# Part of Fleet FMS Connector. License OPL-1.
"""Calls back into FMS when a purchase order that traces back to an
FMS-originated purchase.request is confirmed -- this is how FMS learns
which vendor won the bid analysis and what the approved amount is, so it
can let the driver/admin proceed with taking the vehicle in.

Traceability: purchase.request.line.purchase_lines is a Many2many onto
purchase.order.line (set by OCA's "Create RFQ" wizard), verified against
https://github.com/OCA/purchase-workflow/blob/19.0/purchase_request/models/purchase_request_line.py
on 2026-09-23.

Best-effort delivery only in this first cut: a failed callback is logged,
not retried. If FMS reachability turns out to be flaky in practice, promote
this to a proper queued job (ir.cron sweep of a small log table) rather than
tightening the timeout -- see docs/DOCS_REGISTER.md.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)

CALLBACK_TIMEOUT_SECONDS = 10


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def button_confirm(self):
        result = super().button_confirm()
        for order in self:
            requests_ = order._fms_linked_purchase_requests()
            for pr in requests_:
                order._fms_notify_request_approved(pr)
        return result

    def _fms_linked_purchase_requests(self):
        self.ensure_one()
        lines = self.env["purchase.request.line"].search(
            [("purchase_lines", "in", self.order_line.ids)]
        )
        return lines.mapped("request_id").filtered("x_fms_ref")

    def _fms_notify_request_approved(self, purchase_request):
        self.ensure_one()
        import requests

        icp = self.env["ir.config_parameter"].sudo()
        fms_base_url = icp.get_param("fms_connector.fms_base_url")
        fms_api_key = icp.get_param("fms_connector.fms_outbound_api_key")
        if not fms_base_url or not fms_api_key:
            _logger.warning(
                "fms_connector: PO %s confirmed for FMS request %s but "
                "fms_base_url/fms_outbound_api_key are not configured; "
                "skipping callback.",
                self.name, purchase_request.x_fms_ref,
            )
            return

        payload = {
            "fms_ref": purchase_request.x_fms_ref,
            "odoo_purchase_request_id": purchase_request.id,
            "state": purchase_request.state,
            "po_number": self.name,
            "vendor_name": self.partner_id.display_name,
            "approved_amount": self.amount_total,
            "currency": self.currency_id.name,
        }
        try:
            resp = requests.post(
                f"{fms_base_url.rstrip('/')}/odoo-po-status-callback",
                json=payload,
                headers={"x-api-key": fms_api_key},
                timeout=CALLBACK_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except requests.RequestException:
            _logger.exception(
                "fms_connector: FMS callback failed for PO %s / FMS ref %s",
                self.name, purchase_request.x_fms_ref,
            )
