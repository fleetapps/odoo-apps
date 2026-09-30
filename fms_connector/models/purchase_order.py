# -*- coding: utf-8 -*-
# Part of Fleet FMS Connector. License OPL-1.
"""Calls back into FMS when a purchase order that traces back to an
FMS-originated purchase.request is actually approved -- this is how FMS
learns which vendor won the bid analysis and what the approved amount is,
so it can let the driver/admin proceed with taking the vehicle in.

**Hook the state transition, not the button.** `button_confirm` is not the
approval moment. Under two-step validation it parks the order in
`'to approve'` and approval happens later in `button_approve`:

    if order._approval_allowed():
        order.button_approve()
    else:
        order.write({'state': 'to approve'})

(verbatim from addons/purchase/models/purchase_order.py, 19.0, re-checked
2026-09-24; 18.0 and 20.0 are the same shape). An earlier version of this
file overrode `button_confirm` and notified unconditionally, so FMS was told
"approved, proceed with the vehicle" the moment a large order was merely
*submitted* for approval -- precisely the spend-approval step this customer
runs -- and was never told when the real approval landed. Overriding
`write` catches the single thing that means approved: state entering
`'purchase'`. `button_approve` does `self.write({'state': 'purchase', ...})`,
and one-step `button_confirm` reaches it through `button_approve`, so both
paths are covered, and so is any server action or import that writes the
same state.

Traceability: purchase.request.line.purchase_lines is a Many2many onto
purchase.order.line (set by OCA's "Create RFQ" wizard), verified against
https://github.com/OCA/purchase-workflow/blob/19.0/purchase_request/models/purchase_request_line.py
on 2026-09-23. That request's own state is driven by manual buttons only
(no _compute_state), so it is not a usable signal for "the PO was
approved" -- which is why the payload reports approval from the order.

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

    def write(self, vals):
        if vals.get("state") != "purchase":
            return super().write(vals)
        # Only orders crossing INTO 'purchase' are news; re-writing the same
        # state on an already-approved order must not notify twice.
        entering = self.filtered(lambda order: order.state != "purchase")
        result = super().write(vals)
        for order in entering:
            for purchase_request in order._fms_linked_purchase_requests():
                order._fms_notify_request_approved(purchase_request)
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
                "fms_connector: PO %s approved for FMS request %s but "
                "fms_base_url/fms_outbound_api_key are not configured; "
                "skipping callback.",
                self.name, purchase_request.x_fms_ref,
            )
            return

        payload = {
            "fms_ref": purchase_request.x_fms_ref,
            "odoo_purchase_request_id": purchase_request.id,
            # This method only runs when the order entered 'purchase', so
            # approval is a fact about the order, not about the OCA request's
            # own manually-driven state (which nobody may ever have clicked).
            "state": "approved",
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
