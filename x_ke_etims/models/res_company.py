# -*- coding: utf-8 -*-
"""Which companies eTIMS applies to, and which device signs for each.

eTIMS is for Kenyan companies only -- a company accounting in Kenya, by its
fiscal country -- and among those only for the ones that have an eTIMS device.
A Kenyan company that keeps using a Tremol fiscal device, or has not set one up
yet, posts invoices exactly as before.

Branches
--------
KRA numbers a taxpayer's branches "00" (head office), "01", "02"... and every
branch signs with its own device. An Odoo branch company carries its KRA
branch ID; its invoices are signed by its own device, or by its parent's when
the branch is not a KRA branch of its own (same branch ID as the parent).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ke_etims_device import ENVIRONMENTS, backend_selection


class ResCompany(models.Model):
    _inherit = "res.company"

    ke_etims_bhf_id = fields.Char(
        string="eTIMS Branch ID", size=2, default="00",
        help="KRA's branch ID for this company: 00 for the head office, 01 "
             "upwards for branches registered with KRA.")
    ke_etims_auto_send = fields.Boolean(
        string="Send to eTIMS on posting", default=True,
        help="Queue every posted customer invoice and credit note for signing. "
             "Nothing is sent while posting; the queue sends within a minute.")
    ke_etims_default_backend = fields.Selection(
        selection=backend_selection, string="Default eTIMS Backend", default="demo")
    ke_etims_environment = fields.Selection(
        ENVIRONMENTS, string="eTIMS Environment", default="demo")
    ke_etims_default_payment_type_id = fields.Many2one(
        "ke.etims.code", string="Default eTIMS Payment Type",
        domain=[("code_class", "=", "07")],
        help="KRA's payment type for invoices that do not say otherwise.")
    ke_etims_device_ids = fields.One2many("ke.etims.device", "company_id")
    ke_etims_device_id = fields.Many2one(
        "ke.etims.device", compute="_compute_ke_etims_device_id",
        string="eTIMS Device")
    ke_etims_active = fields.Boolean(
        compute="_compute_ke_etims_device_id", string="eTIMS Active",
        help="A Kenyan company with an initialised eTIMS device.")
    ke_etims_kenyan = fields.Boolean(compute="_compute_ke_etims_kenyan")

    @api.depends("account_fiscal_country_id")
    def _compute_ke_etims_kenyan(self):
        for company in self:
            company.ke_etims_kenyan = company.account_fiscal_country_id.code == "KE"

    def _compute_ke_etims_device_id(self):
        for company in self:
            device = company._ke_etims_device()
            company.ke_etims_device_id = device
            company.ke_etims_active = bool(device) and device.state == "initialised"

    # l10n_ke leaves this for an OSCU module to override. Community's Tremol
    # module reads it to stand aside -- no fiscal-device button, no blocking
    # of Send & Print -- so an invoice is never signed twice.
    def _compute_l10n_ke_oscu_is_active(self):
        super()._compute_l10n_ke_oscu_is_active()
        for company in self:
            if company._ke_etims_is_kenyan():
                device = company._ke_etims_device()
                company.l10n_ke_oscu_is_active = bool(device) and device.state == "initialised"

    # ------------------------------------------------------------- helpers

    def _ke_etims_is_kenyan(self):
        self.ensure_one()
        return self.account_fiscal_country_id.code == "KE"

    def _ke_etims_device(self):
        """The active device that signs this company's sales, or an empty record.

        Its own first; then the nearest parent's, but only while this company
        trades under the same KRA branch ID -- a branch registered with KRA in
        its own right must never sign under head office's device.
        """
        self.ensure_one()
        if not self._ke_etims_is_kenyan():
            return self.env["ke.etims.device"]
        Device = self.env["ke.etims.device"].sudo()
        branch = self.ke_etims_bhf_id or "00"
        for company in reversed(self.parent_ids):
            device = Device.search([("company_id", "=", company.id),
                                    ("bhf_id", "=", branch)], limit=1)
            if device:
                return device
        return Device

    def _ke_etims_enabled(self):
        """Kenyan, with a device (initialised or not): eTIMS is in use here."""
        self.ensure_one()
        return self._ke_etims_is_kenyan() and bool(self._ke_etims_device())

    def action_ke_etims_fetch_branches(self):
        self.ensure_one()
        device = self._ke_etims_device()
        if not device or device.state != "initialised":
            raise UserError(_("Initialise an eTIMS device for %s first.", self.name))
        return device.action_fetch_branches()

    def action_ke_etims_devices(self):
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "x_ke_etims.action_ke_etims_device")
        action["domain"] = [("company_id", "child_of", self.root_id.ids)]
        action["context"] = {"default_company_id": self.id}
        return action

    @api.model
    def _ke_etims_any_kenyan(self, companies):
        return any(company.account_fiscal_country_id.code == "KE" for company in companies)
