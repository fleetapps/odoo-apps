# -*- coding: utf-8 -*-
# Part of Fleet FMS Connector. License OPL-1.
"""Extends OCA's purchase.request (OCA/purchase-workflow, module
'purchase_request') with the reference fields FMS needs and the upsert
logic the controller calls.

Field names on the base model, verified against
https://github.com/OCA/purchase-workflow/blob/19.0/purchase_request/models/purchase_request.py
on 2026-09-23 (also present, unchanged, on the 18.0 branch):
``requested_by`` (Many2one res.users, required), ``assigned_to``
(Many2one res.users), ``picking_type_id`` (Many2one stock.picking.type,
required), ``state`` (draft/to_approve/approved/in_progress/done/rejected),
``line_ids`` (One2many purchase.request.line). ``purchase.request.line``
requires ``product_id`` and has ``product_qty``, ``estimated_cost``,
``analytic_distribution`` (via analytic.mixin), ``purchase_lines``
(Many2many purchase.order.line -- how a line traces to the RFQs/POs
generated from it).
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseRequest(models.Model):
    _inherit = "purchase.request"

    x_fms_ref = fields.Char(
        string="FMS Reference", copy=False, index=True,
        help="FMS purchase_requests.id (UUID). Used to upsert instead of "
             "duplicating on a retried push, and to address the status "
             "callback back to the right FMS row.",
    )
    x_fms_driver_name = fields.Char(string="FMS Driver/Requester Name")
    x_fms_driver_email = fields.Char(
        string="FMS Driver/Requester Email",
        help="Kept even when it successfully matched an Odoo user, so the "
             "original FMS identity is never only inferable from the match.",
    )
    x_fms_vehicle_ref = fields.Char(string="FMS Vehicle Ref")
    x_fms_country = fields.Char(string="FMS Country")
    x_fms_branch = fields.Char(string="FMS Branch / Program Office")

    _sql_fms_ref_unique_per_company = models.Constraint(
        "UNIQUE(x_fms_ref, company_id)",
        "This FMS purchase request has already been pushed to Odoo.",
    )

    @api.model
    def _fms_get_config(self, key, required=False):
        value = self.env["ir.config_parameter"].sudo().get_param(
            "fms_connector." + key
        )
        if required and not value:
            raise UserError(_(
                "fms_connector is not fully configured: missing '%s'. "
                "Set it under Settings > Fleet FMS Connector."
            ) % key)
        return value

    @api.model
    def _fms_resolve_user_by_email(self, email, fallback_param):
        """Match an FMS-supplied email to an Odoo user by login or work
        email, else the configured fallback user. Returns a possibly-empty
        res.users recordset -- callers decide whether an empty match is
        fatal."""
        user = self.env["res.users"]
        if email:
            user = self.env["res.users"].search(
                ["|", ("login", "=ilike", email), ("email", "=ilike", email)],
                limit=1,
            )
        if not user:
            fallback_id = self._fms_get_config(fallback_param)
            if fallback_id:
                user = self.env["res.users"].browse(int(fallback_id)).exists()
        return user

    @api.model
    def fms_upsert(self, payload):
        """Create or update a purchase.request from an FMS payload dict.
        Idempotent on ``x_fms_ref``: a retried push updates the existing
        draft rather than creating a duplicate (only while still draft --
        once a human has moved it along, FMS should not silently rewrite
        it). Returns the purchase.request record.

        Expected payload keys: fms_ref, requested_by_email,
        requested_by_name, country, branch, vehicle_ref, description,
        estimated_cost, currency (informational only -- the request line's
        estimated_cost is in company currency), date_required.
        """
        fms_ref = payload.get("fms_ref")
        if not fms_ref:
            raise UserError(_("Missing required field: fms_ref"))

        existing = self.search([("x_fms_ref", "=", fms_ref)], limit=1)
        if existing and existing.state != "draft":
            return existing

        requestor = self._fms_resolve_user_by_email(
            payload.get("requested_by_email"),
            "default_requestor_id",
        )
        if not requestor:
            raise UserError(_(
                "No Odoo user matches '%s' and no fallback requestor is "
                "configured (Settings > Fleet FMS Connector)."
            ) % payload.get("requested_by_email"))

        approver = self.env["fms.connector.branch.route"].resolve_approver(
            payload.get("country"), payload.get("branch")
        )

        picking_type_id = self._fms_get_config(
            "default_picking_type_id", required=True
        )
        product_id = self._fms_get_config(
            "default_request_product_id", required=True
        )

        vals = {
            "requested_by": requestor.id,
            "assigned_to": approver.id if approver else False,
            "picking_type_id": int(picking_type_id),
            "description": payload.get("description") or "",
            "x_fms_ref": fms_ref,
            "x_fms_driver_name": payload.get("requested_by_name"),
            "x_fms_driver_email": payload.get("requested_by_email"),
            "x_fms_vehicle_ref": payload.get("vehicle_ref"),
            "x_fms_country": payload.get("country"),
            "x_fms_branch": payload.get("branch"),
        }
        line_vals = {
            "product_id": int(product_id),
            "name": payload.get("description") or _("Vehicle maintenance/repair"),
            "product_qty": 1,
            "estimated_cost": payload.get("estimated_cost") or 0.0,
            "date_required": payload.get("date_required") or fields.Date.today(),
        }

        if existing:
            existing.write(vals)
            existing.line_ids.unlink()
            self.env["purchase.request.line"].create(
                dict(line_vals, request_id=existing.id)
            )
            request = existing
        else:
            request = self.create(dict(vals, line_ids=[(0, 0, line_vals)]))

        return request

    def fms_status_payload(self):
        """Shape returned to FMS after a push: enough for FMS to update its
        own row, render "your request is <state>", and -- since FMS sends
        the approval-request email itself, not Odoo -- know who to send it
        to and what link to put in it."""
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "url": self._fms_record_url(),
            "approver_name": self.assigned_to.name if self.assigned_to else None,
            "approver_email": self.assigned_to.email if self.assigned_to else None,
        }

    def _fms_record_url(self):
        """Public URL FMS can put in the approval email it sends. Uses the
        explicit 'fms_connector.public_base_url' override, never bare
        'web.base.url' -- that parameter is rewritten to whatever host an
        admin last logged in from and is unsafe for a link that goes out in
        an email (see res_config_settings.py)."""
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("fms_connector.public_base_url") or icp.get_param(
            "web.base.url", ""
        )
        return f"{base_url}/web#model=purchase.request&view_type=form&id={self.id}"
