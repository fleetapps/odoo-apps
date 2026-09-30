# -*- coding: utf-8 -*-
# Part of Fleet FMS Connector. License OPL-1.
"""Connector-wide settings, stored as ir.config_parameter via the standard
res.config.settings pattern:
https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html#configuration-parameters

Two independent credentials, both required before the bridge works at all:

- ``fms_connector.inbound_api_key``: FMS sends this as ``X-FMS-Api-Key`` on
  every call INTO Odoo (creating a purchase request / an expense). Generated
  here, copied into FMS's own ``organization_integrations`` config.
- ``fms_connector.fms_base_url`` / ``fms_connector.fms_outbound_api_key``:
  used when Odoo calls back OUT to FMS (purchase order approved -> tell FMS
  the winning vendor/amount). ``fms_outbound_api_key`` is the FMS
  organization's own ``api_key`` (the one its ``daily-transfers-api`` and
  friends already validate against) -- FMS issues it, Odoo just stores it.

The defaults below (requestor/approver/picking type/products) exist because
OCA's ``purchase.request`` requires an ``assigned_to``-less flow to work
end-to-end but ``requested_by`` and ``picking_type_id`` are required fields,
and ``hr.expense`` requires ``employee_id``: FMS will not always know a
matching Odoo user, and every purchase request needs *some* picking type /
product even for a service-only line. Per-branch overrides for the requestor
approver live on ``fms.connector.branch.route`` instead, since that varies by
branch/country rather than being instance-wide.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    fms_inbound_api_key = fields.Char(
        string="FMS Inbound API Key",
        config_parameter="fms_connector.inbound_api_key",
        help="FMS sends this as the X-FMS-Api-Key header on every call into "
             "Odoo. Generate one below and paste it into FMS's Odoo "
             "integration settings.",
    )
    fms_public_base_url = fields.Char(
        string="Odoo Public URL (for links FMS sends)",
        config_parameter="fms_connector.public_base_url",
        help="Explicit override for the base URL used in approval links "
             "FMS emails out. Do NOT rely on 'web.base.url' for this: it "
             "gets silently rewritten to whatever host an admin last logged "
             "in from (see Settings > Technical > System Parameters), which "
             "is exactly wrong for a link meant to go out in an email. Set "
             "this once to your real public URL, e.g. https://odoo.example.com.",
    )
    fms_base_url = fields.Char(
        string="FMS Base URL",
        config_parameter="fms_connector.fms_base_url",
        help="e.g. https://<project-ref>.supabase.co/functions/v1 -- used "
             "when Odoo calls back into FMS after a purchase order is "
             "confirmed.",
    )
    fms_outbound_api_key = fields.Char(
        string="FMS Outbound API Key",
        config_parameter="fms_connector.fms_outbound_api_key",
        help="The FMS organization's own api_key (from its organizations "
             "table) -- sent as x-api-key when Odoo calls back into FMS.",
    )
    fms_default_requestor_id = fields.Many2one(
        "res.users",
        string="Fallback Requestor",
        config_parameter="fms_connector.default_requestor_id",
        help="Used when a purchase request arrives from FMS and no Odoo "
             "user matches the driver/admin's email.",
    )
    fms_default_approver_id = fields.Many2one(
        "res.users",
        string="Fallback Approver",
        config_parameter="fms_connector.default_approver_id",
        help="Used when the request's country/branch has no row in "
             "Fleet FMS > Branch Routing.",
    )
    fms_default_picking_type_id = fields.Many2one(
        "stock.picking.type",
        string="Default Purchase Request Picking Type",
        config_parameter="fms_connector.default_picking_type_id",
        help="purchase.request requires a picking type even for a "
             "service-only line (vehicle repair). Point this at whichever "
             "operation type your team uses for non-stock requests.",
    )
    fms_default_request_product_id = fields.Many2one(
        "product.product",
        string="Default Purchase Request Product",
        config_parameter="fms_connector.default_request_product_id",
        domain=[("purchase_ok", "=", True)],
        help="Placeholder product used on the purchase request line when "
             "the driver's request has no specific part/product -- e.g. a "
             "generic 'Vehicle Maintenance & Repair Service' product.",
    )
    fms_default_expense_product_id = fields.Many2one(
        "product.product",
        string="Default Expense Category",
        config_parameter="fms_connector.default_expense_product_id",
        domain=[("can_be_expensed", "=", True)],
        help="Expense category (product) used for expenses pushed from FMS "
             "-- e.g. a generic 'Vehicle Maintenance & Repair' expense "
             "category.",
    )
    fms_default_expense_employee_id = fields.Many2one(
        "hr.employee",
        string="Fallback Expense Employee",
        config_parameter="fms_connector.default_expense_employee_id",
        help="Used when the FMS employee_email on an incoming expense "
             "doesn't match any Odoo employee's work email.",
    )

    def action_fms_generate_inbound_api_key(self):
        """Generate a fresh inbound API key and save it immediately (not
        just staged in the form) so it can be copied into FMS right away."""
        self.ensure_one()
        import secrets

        key = "fmsk_" + secrets.token_urlsafe(32)
        self.env["ir.config_parameter"].sudo().set_param(
            "fms_connector.inbound_api_key", key
        )
        self.fms_inbound_api_key = key
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("FMS API key generated"),
                "message": self.env._(
                    "Copy it into FMS now -- it will not be shown again "
                    "in full."
                ),
                "sticky": False,
            },
        }
