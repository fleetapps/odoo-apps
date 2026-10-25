# -*- coding: utf-8 -*-
"""A bill of materials, told to KRA as the composition of the item it makes.

Quantities are per one unit of the finished product, in each product's own
unit, which is how KRA reads cpstQty. A composition is a latest value: sending
it again after the BoM changes replaces the previous one.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.x_ke_etims.backends import REGISTRY, Composition


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    ke_etims_can_send = fields.Boolean(compute="_compute_ke_etims_can_send")
    ke_etims_state = fields.Selection(
        selection=lambda self: self.env["ke.etims.document"]._fields["state"].selection,
        compute="_compute_ke_etims_can_send", string="eTIMS Status")

    @api.depends_context("company")
    @api.depends("company_id")
    def _compute_ke_etims_can_send(self):
        for bom in self:
            company = bom.company_id or self.env.company
            device = company._ke_etims_device()
            cls = REGISTRY.get(device.backend) if device else None
            bom.ke_etims_can_send = bool(cls) and cls.supports("save_item_composition")
            document = self.env["ke.etims.document"].sudo().search([
                ("res_model", "=", "mrp.bom"), ("res_id", "=", bom.id),
                ("kind", "=", "item_composition"), ("state", "!=", "cancelled")],
                limit=1) if bom.id else None
            bom.ke_etims_state = document.state if document else False

    def _ke_etims_company(self):
        return self.company_id or self.env.company

    def action_ke_etims_send_composition(self):
        Document = self.env["ke.etims.document"].sudo()
        for bom in self:
            if not bom.ke_etims_can_send:
                raise UserError(_("The eTIMS backend of %s does not take item compositions.",
                                  bom._ke_etims_company().name))
            existing = Document.search([
                ("res_model", "=", "mrp.bom"), ("res_id", "=", bom.id),
                ("kind", "=", "item_composition"), ("state", "!=", "cancelled")], limit=1)
            if existing and existing.state != "sending":
                existing.write({"state": "to_send", "attempts": 0, "next_try": False})
                document = existing
            else:
                document = Document._ke_etims_enqueue(bom, "item_composition")
            document.action_send_now()
        return True

    # ------------------------------------------------- the eTIMS source API

    def _ke_etims_device(self):
        self.ensure_one()
        return self._ke_etims_company()._ke_etims_device()

    def _ke_etims_products(self):
        self.ensure_one()
        finished = self.product_id or self.product_tmpl_id.product_variant_id
        return finished, self.bom_line_ids.product_id

    def _ke_etims_validate(self, document):
        self.ensure_one()
        errors = []
        finished, components = self._ke_etims_products()
        if not self.bom_line_ids:
            errors.append(_("%s has no components.", self.display_name))
        for product in finished | components:
            template = product.product_tmpl_id
            if not template._ke_etims_registration(document.device_id) and not \
                    template._ke_etims_register(document.device_id, document=document,
                                                raise_errors=False):
                errors.append(_("%s is not registered with eTIMS.", template.display_name))
        return errors

    def _ke_etims_prepare(self, document):
        self.ensure_one()
        device = document.device_id
        finished, _components = self._ke_etims_products()
        finished_code = finished.product_tmpl_id._ke_etims_registration(device).item_code
        bom_quantity = self.product_uom_id._compute_quantity(
            self.product_qty, self.product_tmpl_id.uom_id) or 1.0
        compositions = []
        for line in self.bom_line_ids:
            component = line.product_id
            quantity = line.product_uom_id._compute_quantity(
                line.product_qty, component.uom_id) / bom_quantity
            compositions.append(Composition(
                item_code=finished_code,
                component_item_code=component.product_tmpl_id._ke_etims_registration(
                    device).item_code,
                component_quantity=round(quantity, 2),
                operator=device._ke_etims_operator(),
            ))
        return compositions

    def _ke_etims_on_signed(self, document):
        self.message_post(body=_("Composition sent to eTIMS."))

    def _ke_etims_on_failed(self, document):
        document._ke_etims_schedule_activity()
