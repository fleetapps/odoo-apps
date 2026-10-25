# -*- coding: utf-8 -*-
"""Supplier invoices from eTIMS, as draft bills, confirmed back as purchases.

A supplier who signs a sale with our PIN declares it to KRA; eTIMS then lists
it for us to confirm. Each one fetched is kept here as KRA sent it -- the
supplier's own items, quantities and VAT-inclusive amounts -- and turned into a
draft vendor bill for the supplier matched by PIN. Its lines are linked to our
products, by a mapping remembered per supplier item or by the wizard.

Posting the bill confirms the purchase to eTIMS with the supplier's figures,
not ours: KRA matches the confirmation against the sale the supplier declared.
"""

from decimal import Decimal

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.x_ke_etims.backends import Line, Purchase
from odoo.addons.x_ke_etims.backends.base import money


def _dec(value):
    return Decimal(str(value or 0))


class KeEtimsPurchase(models.Model):
    _name = "ke.etims.purchase"
    _description = "eTIMS Supplier Invoice"
    _inherit = ["mail.thread"]
    _order = "sale_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    device_id = fields.Many2one("ke.etims.device", required=True, index=True,
                                ondelete="restrict")
    company_id = fields.Many2one(related="device_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    provider_ref = fields.Char(required=True, index=True,
                               help="The record's identity at KRA or the provider.")
    supplier_pin = fields.Char(string="Supplier PIN")
    supplier_name = fields.Char()
    supplier_bhf_id = fields.Char(string="Supplier Branch")
    supplier_invoice_no = fields.Integer(string="Supplier Invoice No.")
    supplier_cu_invoice = fields.Char(string="Supplier CU Invoice No.",
                                      help="SCU ID / invoice number, where KRA sends it.")
    receipt_type = fields.Char(help="P for a sale to us, R for the supplier's credit note.")
    payment_type = fields.Char()
    sale_date = fields.Date()
    confirmed_at = fields.Datetime()
    total_taxable = fields.Monetary()
    total_tax = fields.Monetary(string="VAT")
    total_amount = fields.Monetary()
    remark = fields.Char()
    partner_id = fields.Many2one("res.partner", string="Supplier", check_company=True,
                                 help="Matched by PIN; set it by hand if no contact has the PIN.")
    move_id = fields.Many2one("account.move", string="Vendor Bill", copy=False,
                              check_company=True)
    line_ids = fields.One2many("ke.etims.purchase.line", "purchase_id")
    state = fields.Selection(
        [("new", "New"), ("billed", "Bill drafted"), ("confirming", "Confirming"),
         ("confirmed", "Confirmed"), ("rejected", "Rejected")],
        default="new", required=True, tracking=True)
    unmapped_count = fields.Integer(compute="_compute_unmapped_count")

    _device_ref_unique = models.Constraint(
        "UNIQUE(device_id, provider_ref)", "This supplier invoice was already fetched.")

    @api.depends("supplier_name", "supplier_invoice_no")
    def _compute_name(self):
        for purchase in self:
            purchase.name = "%s #%s" % (purchase.supplier_name or purchase.supplier_pin or "?",
                                        purchase.supplier_invoice_no or "?")

    @api.depends("line_ids.product_id")
    def _compute_unmapped_count(self):
        for purchase in self:
            purchase.unmapped_count = len(purchase.line_ids.filtered(lambda l: not l.product_id))

    # --------------------------------------------------------------- import

    @api.model
    def _ke_etims_store(self, device, rows):
        created = self.browse()
        for row in rows:
            ref = row.get("provider_ref")
            if not ref or self.search_count([("device_id", "=", device.id),
                                             ("provider_ref", "=", ref)]):
                continue
            purchase = self.create({
                "device_id": device.id,
                "provider_ref": ref,
                "supplier_pin": (row.get("supplier_pin") or "").upper() or False,
                "supplier_name": row.get("supplier_name"),
                "supplier_bhf_id": row.get("supplier_bhf_id"),
                "supplier_invoice_no": row.get("supplier_invoice_no") or 0,
                "supplier_cu_invoice": row.get("supplier_cu_invoice"),
                "receipt_type": row.get("receipt_type"),
                "payment_type": row.get("payment_type"),
                "sale_date": row.get("sale_date"),
                "confirmed_at": row.get("confirmed_at"),
                "total_taxable": float(row.get("total_taxable") or 0),
                "total_tax": float(row.get("total_tax") or 0),
                "total_amount": float(row.get("total_amount") or 0),
                "remark": row.get("remark"),
                "line_ids": [Command.create({
                    "sequence": line.get("seq") or index,
                    "item_code": line.get("item_code"),
                    "item_class_code": line.get("item_class_code"),
                    "name": line.get("name") or line.get("item_code") or "?",
                    "packaging_unit": line.get("packaging_unit"),
                    "quantity_unit": line.get("quantity_unit"),
                    "quantity": float(line.get("quantity") or 0),
                    "unit_price": float(line.get("unit_price") or 0),
                    "supply_amount": float(line.get("supply_amount") or 0),
                    "discount_amount": float(line.get("discount_amount") or 0),
                    "tax_type": line.get("tax_type"),
                    "taxable_amount": float(line.get("taxable_amount") or 0),
                    "tax_amount": float(line.get("tax_amount") or 0),
                    "total_amount": float(line.get("total_amount") or 0),
                    "provider_ref": line.get("provider_ref"),
                }) for index, line in enumerate(row.get("lines") or [], start=1)],
            })
            purchase._ke_etims_match_supplier()
            purchase.line_ids._ke_etims_apply_known_mapping()
            created |= purchase
        return created

    def _ke_etims_match_supplier(self):
        for purchase in self.filtered(lambda p: not p.partner_id and p.supplier_pin):
            partner = self.env["res.partner"].search([
                ("vat", "=ilike", purchase.supplier_pin),
                ("company_id", "in", [False] + purchase.company_id.parent_ids.ids),
            ], order="is_company desc, id", limit=1)
            purchase.partner_id = partner.commercial_partner_id

    # ----------------------------------------------------------------- bills

    def _ke_etims_create_bills(self):
        for purchase in self.filtered(lambda p: p.state == "new" and p.partner_id
                                      and not p.move_id):
            purchase._ke_etims_create_bill()

    def action_create_bill(self):
        for purchase in self:
            if not purchase.partner_id:
                raise UserError(_("Choose the supplier first: no contact has the PIN %s.",
                                  purchase.supplier_pin or "-"))
            if not purchase.move_id:
                purchase._ke_etims_create_bill()
        return self.action_open_bill() if len(self) == 1 else True

    def _ke_etims_purchase_tax(self, tax_type):
        company = self.company_id
        return self.env["account.tax"].search([
            ("company_id", "in", company.parent_ids.ids),
            ("type_tax_use", "=", "purchase"),
            ("ke_etims_tax_type_id.code", "=", tax_type),
            ("price_include", "=", False),
            ("amount_type", "=", "percent"),
        ], order="sequence, id", limit=1)

    def _ke_etims_create_bill(self):
        self.ensure_one()
        lines = []
        for line in self.line_ids:
            excluded = _dec(line.taxable_amount) - _dec(line.tax_amount)
            quantity = _dec(line.quantity) or Decimal(1)
            tax = self._ke_etims_purchase_tax(line.tax_type) if line.tax_type else None
            lines.append(Command.create({
                "product_id": line.product_id.id or False,
                "name": line.name,
                "quantity": float(quantity),
                "price_unit": float(excluded / quantity),
                "tax_ids": [Command.set(tax.ids if tax else [])],
            }))
        move = self.env["account.move"].with_company(self.company_id).create({
            "move_type": "in_refund" if self.receipt_type == "R" else "in_invoice",
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "invoice_date": self.sale_date or fields.Date.context_today(self),
            "ref": str(self.supplier_invoice_no or ""),
            # KRA's own record of the supplier's invoice: its CU number where
            # KRA gives one, else the identity KRA lists it under.
            "ke_etims_supplier_invoice_ref": self.supplier_cu_invoice or self.provider_ref,
            "ke_etims_purchase_id": self.id,
            "invoice_line_ids": lines,
        })
        self.write({"move_id": move.id, "state": "billed"})
        self.message_post(body=_("Draft bill %s created.", move._get_html_link()))
        return move

    def action_open_bill(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move",
                "res_id": self.move_id.id, "view_mode": "form"}

    def action_map_products(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Link supplier items to products"),
            "res_model": "ke.etims.purchase.map",
            "view_mode": "form",
            "target": "new",
            "context": {"default_purchase_id": self.id},
        }

    def action_reject(self):
        self.write({"state": "rejected"})
        return True

    # ------------------------------------------------- the eTIMS source API

    def _ke_etims_device(self):
        self.ensure_one()
        return self.device_id

    def _ke_etims_validate(self, document):
        self.ensure_one()
        errors = []
        if self.move_id.state != "posted":
            errors.append(_("The bill for %s is not posted.", self.display_name))
        for line in self.line_ids:
            if not line.product_id:
                errors.append(_("%s is not linked to a product.", line.name))
                continue
            template = line.product_id.product_tmpl_id
            if not template._ke_etims_registration(self.device_id) and not \
                    template._ke_etims_register(self.device_id, document=document,
                                                raise_errors=False):
                errors.append(_("%s is not registered with eTIMS.", template.display_name))
        return errors

    def _ke_etims_prepare(self, document):
        self.ensure_one()
        device = self.device_id
        if not document.kra_sequence_no:
            document.kra_sequence_no = device._ke_etims_next_purchase_no()
        lines = []
        for seq, line in enumerate(self.line_ids.sorted("sequence"), start=1):
            template = line.product_id.product_tmpl_id
            registration = template._ke_etims_registration(device)
            quantity = money(line.quantity)
            lines.append(Line(
                seq=seq,
                item_code=registration.item_code or template.ke_etims_item_code,
                item_class_code=template.ke_etims_item_class_id.code or line.item_class_code,
                name=template.name,
                quantity=quantity,
                unit_price=money(line.unit_price),
                tax_type=line.tax_type,
                supply_amount=money(line.supply_amount or line.taxable_amount),
                discount_amount=money(line.discount_amount),
                taxable_amount=money(line.taxable_amount),
                tax_amount=money(line.tax_amount),
                packaging_unit=line.packaging_unit or template.ke_etims_packaging_unit_id.code,
                quantity_unit=line.quantity_unit or template.ke_etims_quantity_unit_id.code,
                package_quantity=quantity,
                provider_item_id=registration.provider_item_id or None,
                supplier_item_code=line.item_code,
                supplier_item_class_code=line.item_class_code,
                supplier_item_name=line.name,
            ))
        return Purchase(
            invoice_no=document.kra_sequence_no,
            lines=lines,
            purchase_date=self.move_id.invoice_date or self.sale_date,
            tax_rates=device._ke_etims_tax_rates(),
            operator=device._ke_etims_operator(),
            payment_type=self.payment_type or "01",
            supplier_pin=self.supplier_pin,
            supplier_bhf_id=self.supplier_bhf_id,
            supplier_name=self.supplier_name,
            supplier_invoice_no=self.supplier_invoice_no or None,
            registration_type="A",
            receipt_type="R" if self.receipt_type == "R" else "P",
            status="02",
            confirmed_at=document.confirmed_at,
            provider_ref=self.provider_ref,
        )

    def _ke_etims_on_signed(self, document):
        self.state = "confirmed"
        self.move_id.message_post(body=_("Purchase confirmed to eTIMS (purchase no. %s).",
                                         document.kra_sequence_no))

    def _ke_etims_on_failed(self, document):
        self.message_post(body=_("eTIMS refused the confirmation: %s", document.last_error))
        document._ke_etims_schedule_activity()


class KeEtimsPurchaseLine(models.Model):
    _name = "ke.etims.purchase.line"
    _description = "eTIMS Supplier Invoice Line"
    _order = "purchase_id, sequence, id"

    purchase_id = fields.Many2one("ke.etims.purchase", required=True, ondelete="cascade",
                                  index=True)
    company_id = fields.Many2one(related="purchase_id.company_id", store=True)
    currency_id = fields.Many2one(related="purchase_id.currency_id")
    sequence = fields.Integer()
    item_code = fields.Char(string="Supplier Item Code")
    item_class_code = fields.Char(string="Item Class")
    name = fields.Char(string="Supplier Item", required=True)
    packaging_unit = fields.Char()
    quantity_unit = fields.Char()
    quantity = fields.Float()
    unit_price = fields.Monetary(help="VAT included, as the supplier declared it.")
    supply_amount = fields.Monetary()
    discount_amount = fields.Monetary()
    tax_type = fields.Char(string="Tax Type")
    taxable_amount = fields.Monetary()
    tax_amount = fields.Monetary(string="VAT")
    total_amount = fields.Monetary()
    provider_ref = fields.Char()
    product_id = fields.Many2one("product.product", string="Our Product")

    def _ke_etims_apply_known_mapping(self):
        Map = self.env["ke.etims.product.map"]
        for line in self.filtered(lambda l: not l.product_id):
            partner = line.purchase_id.partner_id
            mapping = Map.search([("partner_id", "=", partner.id),
                                  ("supplier_item_code", "=", line.item_code)], limit=1) \
                if partner and line.item_code else Map
            if mapping:
                line.product_id = mapping.product_id
                continue
            if partner and line.item_code:
                seller = self.env["product.supplierinfo"].search([
                    ("partner_id", "=", partner.id), ("product_code", "=", line.item_code)],
                    limit=1)
                if seller:
                    line.product_id = seller.product_id or seller.product_tmpl_id.product_variant_id


class KeEtimsProductMap(models.Model):
    """Which of our products a supplier's eTIMS item is, remembered."""

    _name = "ke.etims.product.map"
    _description = "eTIMS Supplier Item Mapping"

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade", index=True)
    supplier_item_code = fields.Char(required=True, index=True)
    supplier_item_name = fields.Char()
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade")

    _partner_item_unique = models.Constraint(
        "UNIQUE(partner_id, supplier_item_code)", "This supplier item is already mapped.")
