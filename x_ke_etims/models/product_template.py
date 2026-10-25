# -*- coding: utf-8 -*-
"""Products as KRA items.

A product has to be registered as an item on a device before it can be sold
under it. Registration needs KRA's classification of the product -- item
class, product type, packaging and quantity unit, origin -- and gives it an
item code, which this module generates in the pattern KRA's specification
recommends (section 4.19): origin country, product type, packaging unit,
quantity unit, then a seven-digit running number, e.g. KE2NTU0000012. The code
is fixed once generated; KRA only requires it to be unique per taxpayer.

Registration is per device, because KRA keeps items per branch and DigiTax
keeps an id of its own for each. All variants of a product are one item.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..backends import EtimsError, Item
from .ke_etims_code import (
    CODE_CLASS_COUNTRY, CODE_CLASS_PACKAGING_UNIT, CODE_CLASS_QUANTITY_UNIT,
)

PRODUCT_TYPES = [
    ("1", "Raw material"),
    ("2", "Finished product"),
    ("3", "Service"),
]


class ProductTemplate(models.Model):
    _inherit = "product.template"

    ke_etims_item_class_id = fields.Many2one(
        "ke.etims.item.class", string="KRA Item Class",
        help="KRA's classification of the product (UNSPSC based).")
    ke_etims_product_type = fields.Selection(
        PRODUCT_TYPES, string="KRA Product Type",
        compute="_compute_ke_etims_product_type", store=True, readonly=False)
    ke_etims_packaging_unit_id = fields.Many2one(
        "ke.etims.code", string="Packaging Unit",
        domain=[("code_class", "=", CODE_CLASS_PACKAGING_UNIT)],
        default=lambda self: self.env["ke.etims.code"]._ke_etims_find(
            CODE_CLASS_PACKAGING_UNIT, "NT"))
    ke_etims_quantity_unit_id = fields.Many2one(
        "ke.etims.code", string="Quantity Unit",
        domain=[("code_class", "=", CODE_CLASS_QUANTITY_UNIT)],
        default=lambda self: self.env["ke.etims.code"]._ke_etims_find(
            CODE_CLASS_QUANTITY_UNIT, "U"))
    ke_etims_origin_country_id = fields.Many2one(
        "ke.etims.code", string="Origin Country",
        domain=[("code_class", "=", CODE_CLASS_COUNTRY)],
        default=lambda self: self.env["ke.etims.code"]._ke_etims_find(
            CODE_CLASS_COUNTRY, "KE"))
    ke_etims_item_code = fields.Char(
        string="KRA Item Code", readonly=True, copy=False, index=True,
        help="Generated when the product is first registered with eTIMS.")
    ke_etims_registration_ids = fields.One2many(
        "ke.etims.item.registration", "product_tmpl_id", string="eTIMS Registrations")
    ke_etims_registered = fields.Boolean(
        compute="_compute_ke_etims_registered", string="Registered with eTIMS")
    ke_etims_show = fields.Boolean(compute="_compute_ke_etims_registered")

    @api.depends("type")
    def _compute_ke_etims_product_type(self):
        for template in self:
            if not template.ke_etims_product_type:
                template.ke_etims_product_type = "3" if template.type == "service" else "2"

    @api.depends_context("company")
    @api.depends("ke_etims_registration_ids.state", "ke_etims_registration_ids.device_id")
    def _compute_ke_etims_registered(self):
        company = self.env.company
        device = company._ke_etims_device() if company._ke_etims_is_kenyan() else None
        for template in self:
            template.ke_etims_show = company._ke_etims_is_kenyan()
            template.ke_etims_registered = bool(device) and bool(
                template._ke_etims_registration(device))

    # ------------------------------------------------------------- helpers

    def _ke_etims_registration(self, device):
        self.ensure_one()
        return self.sudo().ke_etims_registration_ids.filtered(
            lambda r: r.device_id == device and r.state == "registered")[:1]

    def _ke_etims_missing(self):
        """What KRA needs to know about the product that it does not say yet."""
        self.ensure_one()
        labels = {
            "ke_etims_item_class_id": _("KRA item class"),
            "ke_etims_product_type": _("KRA product type"),
            "ke_etims_packaging_unit_id": _("packaging unit"),
            "ke_etims_quantity_unit_id": _("quantity unit"),
            "ke_etims_origin_country_id": _("origin country"),
        }
        return [label for field, label in labels.items() if not self[field]]

    def _ke_etims_tax_type(self, company):
        """The tax type of the product's sales tax in that company."""
        self.ensure_one()
        # A branch uses its parents' taxes.
        taxes = self.sudo().taxes_id.filtered(lambda t: t.company_id in company.parent_ids)
        band = taxes.filtered("ke_etims_tax_type_id")[:1].ke_etims_tax_type_id.code
        return band or self.ke_etims_item_class_id.tax_type_code or False

    def _ke_etims_ensure_item_code(self):
        self.ensure_one()
        if self.ke_etims_item_code:
            return self.ke_etims_item_code
        sequence = int(self.env["ir.sequence"].sudo().next_by_code("ke.etims.item.code") or 0)
        code = "%s%s%s%s%07d" % (
            self.ke_etims_origin_country_id.code, self.ke_etims_product_type,
            self.ke_etims_packaging_unit_id.code, self.ke_etims_quantity_unit_id.code,
            sequence)
        if len(code) > 20:
            raise UserError(_("The KRA item code %s would be longer than 20 characters.", code))
        self.sudo().ke_etims_item_code = code
        return code

    def _ke_etims_register(self, device, document=None, raise_errors=True):
        """Register the product on one device; returns the registration."""
        self.ensure_one()
        missing = self._ke_etims_missing()
        if missing:
            message = _("%(product)s needs its %(missing)s before it can be "
                        "registered with eTIMS.",
                        product=self.display_name, missing=", ".join(missing))
            if raise_errors:
                raise UserError(message)
            return None
        tax_type = self._ke_etims_tax_type(device.company_id)
        if not tax_type:
            message = _("%s has no sales tax with a KRA tax type, so eTIMS cannot "
                        "know how it is taxed.", self.display_name)
            if raise_errors:
                raise UserError(message)
            return None
        registration = self.sudo().ke_etims_registration_ids.filtered(
            lambda r: r.device_id == device)[:1]
        item = Item(
            item_code=self._ke_etims_ensure_item_code(),
            item_class_code=self.ke_etims_item_class_id.code,
            product_type=self.ke_etims_product_type,
            name=self.name,
            origin_country=self.ke_etims_origin_country_id.code,
            packaging_unit=self.ke_etims_packaging_unit_id.code,
            quantity_unit=self.ke_etims_quantity_unit_id.code,
            tax_type=tax_type,
            default_price=self.list_price,
            operator=device._ke_etims_operator(),
            barcode=self.barcode or None,
            active=self.active,
            provider_item_id=registration.provider_item_id or None,
            callback_url=device.sudo().callback_url or None,
        )
        try:
            provider_item_id = device._ke_etims_adapter(document=document).register_item(item)
        except EtimsError as exc:
            vals = {"state": "failed", "last_error": str(exc)}
            if registration:
                registration.write(vals)
            else:
                self.env["ke.etims.item.registration"].sudo().create({
                    **vals, "product_tmpl_id": self.id, "device_id": device.id,
                    "item_code": item.item_code})
            if raise_errors:
                raise UserError(_("eTIMS refused %(product)s: %(error)s",
                                  product=self.display_name, error=exc)) from exc
            return None
        vals = {"state": "registered", "last_error": False, "item_code": item.item_code,
                "registered_at": fields.Datetime.now(),
                "provider_item_id": provider_item_id or registration.provider_item_id}
        if registration:
            registration.write(vals)
        else:
            registration = self.env["ke.etims.item.registration"].sudo().create({
                **vals, "product_tmpl_id": self.id, "device_id": device.id})
        return registration

    def action_ke_etims_register(self):
        company = self.env.company
        if not company._ke_etims_is_kenyan():
            raise UserError(_("eTIMS is only available to Kenyan companies."))
        devices = self.env["ke.etims.device"]._ke_etims_devices(
            self.env.companies.filtered(lambda c: c.account_fiscal_country_id.code == "KE"))
        if not devices:
            raise UserError(_("There is no initialised eTIMS device to register with."))
        for template in self:
            for device in devices:
                template._ke_etims_register(device)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("eTIMS"), "type": "success",
                       "message": _("Registered with eTIMS."),
                       "next": {"type": "ir.actions.client", "tag": "soft_reload"}},
        }
