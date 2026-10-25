# -*- coding: utf-8 -*-
"""KRA's reference data, as KRA publishes it.

Codes, item classifications and notices are the same for every taxpayer, so
they are stored once rather than per company, and replaced by each sync.
Nothing in this module chooses from a hard-coded list of KRA codes: a tax
type, a unit or a credit-note reason is a row here, and the row came from KRA
(or from the provider's equivalent, or from the seed file this module ships
with the handful of values the OSCU specification v2.0 fixes, which the first
sync overwrites).
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

#: KRA code classes the module reads, by number. The list of classes and their
#: codes still comes from KRA; these numbers only say which one is which.
CODE_CLASS_TAX_TYPE = "04"
CODE_CLASS_COUNTRY = "05"
CODE_CLASS_PAYMENT_TYPE = "07"
CODE_CLASS_QUANTITY_UNIT = "10"
CODE_CLASS_STOCK_IO_TYPE = "12"
CODE_CLASS_PACKAGING_UNIT = "17"
CODE_CLASS_CREDIT_NOTE_REASON = "32"


class KeEtimsCode(models.Model):
    _name = "ke.etims.code"
    _description = "KRA eTIMS Code"
    _order = "code_class, sort_order, code"
    _rec_names_search = ["code", "name"]

    code_class = fields.Char(required=True, index=True,
                             help="KRA's code class, e.g. 04 for tax types.")
    class_name = fields.Char()
    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True)
    description = fields.Char()
    sort_order = fields.Integer()
    active = fields.Boolean(default=True)
    user_defined_1 = fields.Char(help="For tax types (class 04), KRA's rate in percent.")
    user_defined_2 = fields.Char()
    user_defined_3 = fields.Char()
    rate = fields.Float(compute="_compute_rate", store=True, digits=(5, 2),
                        help="The tax rate KRA publishes for a tax type.")

    _class_code_unique = models.Constraint(
        "UNIQUE(code_class, code)", "A KRA code appears once per code class.")

    @api.depends("code_class", "user_defined_1")
    def _compute_rate(self):
        for record in self:
            rate = 0.0
            if record.code_class == CODE_CLASS_TAX_TYPE and record.user_defined_1:
                try:
                    rate = float(record.user_defined_1)
                except ValueError:
                    rate = 0.0
            record.rate = rate

    @api.depends("code", "name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = "%s - %s" % (record.code, record.name) \
                if record.code and record.name and record.code not in record.name \
                else (record.name or record.code)

    @api.model
    def _ke_etims_find(self, code_class, code):
        if not code:
            return self.browse()
        return self.with_context(active_test=False).search(
            [("code_class", "=", code_class), ("code", "=", code)], limit=1)

    @api.model
    def _ke_etims_store(self, classes):
        """Upsert the code classes a backend returned. Returns the count."""
        count = 0
        existing = {
            (record.code_class, record.code): record
            for record in self.with_context(active_test=False).search([])
        }
        for cls in classes:
            class_id = cls.get("class")
            if not class_id:
                continue
            for code in cls.get("codes") or []:
                if not code.get("code"):
                    continue
                user_defined = tuple(code.get("user_defined") or ()) + (None, None, None)
                vals = {
                    "code_class": class_id,
                    "class_name": cls.get("name"),
                    "code": code["code"],
                    "name": code.get("name") or code["code"],
                    "description": code.get("description"),
                    "sort_order": int(code.get("sort_order") or 0),
                    "active": code.get("active", True),
                    "user_defined_1": user_defined[0],
                    "user_defined_2": user_defined[1],
                    "user_defined_3": user_defined[2],
                }
                record = existing.get((class_id, code["code"]))
                if record:
                    record.write(vals)
                else:
                    existing[(class_id, code["code"])] = self.create(vals)
                count += 1
        return count


class KeEtimsItemClass(models.Model):
    _name = "ke.etims.item.class"
    _description = "KRA eTIMS Item Classification"
    _order = "code"
    _rec_names_search = ["code", "name"]

    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True)
    level = fields.Integer()
    tax_type_code = fields.Char(string="Tax Type", help="KRA's suggested tax type letter.")
    major_target = fields.Boolean()
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint("UNIQUE(code)", "An item class appears once.")

    @api.depends("code", "name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = "%s %s" % (record.code, record.name or "")

    @api.model
    def _ke_etims_store(self, rows):
        existing = {r.code: r for r in self.with_context(active_test=False).search([])}
        count = 0
        for row in rows:
            if not row.get("code"):
                continue
            vals = {
                "code": row["code"],
                "name": row.get("name") or row["code"],
                "level": int(row.get("level") or 0),
                "tax_type_code": row.get("tax_type"),
                "major_target": bool(row.get("major_target")),
                "active": row.get("active", True),
            }
            if row["code"] in existing:
                existing[row["code"]].write(vals)
            else:
                existing[row["code"]] = self.create(vals)
            count += 1
        return count


class KeEtimsNotice(models.Model):
    _name = "ke.etims.notice"
    _description = "KRA eTIMS Notice"
    _order = "registered_at desc, number desc"

    number = fields.Integer(index=True)
    name = fields.Char(string="Title", required=True)
    contents = fields.Text()
    url = fields.Char(string="Details")
    registered_at = fields.Datetime()

    @api.model
    def _ke_etims_store(self, rows):
        count = 0
        for row in rows:
            title = row.get("title") or ("Notice %s" % row.get("number"))
            existing = self.search([("number", "=", row.get("number"))], limit=1) \
                if row.get("number") else self.browse()
            vals = {
                "number": row.get("number") or 0,
                "name": title,
                "contents": row.get("contents"),
                "url": row.get("url"),
                "registered_at": row.get("registered_at"),
            }
            if existing:
                existing.write(vals)
            else:
                self.create(vals)
            count += 1
        return count


class KeEtimsBranch(models.Model):
    """A branch of the business as KRA has it registered, to be matched with
    the Odoo company (or branch company) that trades as it."""

    _name = "ke.etims.branch"
    _description = "KRA eTIMS Branch"
    _order = "company_id, bhf_id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True,
                                 help="The company whose PIN the branch belongs to.")
    bhf_id = fields.Char(string="Branch ID", required=True)
    name = fields.Char()
    status = fields.Char()
    is_head_office = fields.Boolean()
    linked_company_id = fields.Many2one(
        "res.company", string="Odoo Company",
        help="The company or branch company that trades as this KRA branch. "
             "Linking sets its eTIMS branch ID.")

    _company_branch_unique = models.Constraint(
        "UNIQUE(company_id, bhf_id)", "A KRA branch appears once per company.")

    def write(self, vals):
        result = super().write(vals)
        if vals.get("linked_company_id"):
            for branch in self:
                branch.linked_company_id.sudo().ke_etims_bhf_id = branch.bhf_id
        return result


class KeEtimsItemRegistration(models.Model):
    """That a product is registered as an item on one device.

    KRA keeps items per taxpayer and branch, and DigiTax keeps its own id for
    each, so registration is per device rather than a flag on the product.
    """

    _name = "ke.etims.item.registration"
    _description = "eTIMS Item Registration"
    _order = "registered_at desc, id desc"

    product_tmpl_id = fields.Many2one("product.template", required=True,
                                      ondelete="cascade", index=True)
    device_id = fields.Many2one("ke.etims.device", required=True, ondelete="cascade",
                                index=True)
    company_id = fields.Many2one(related="device_id.company_id", store=True)
    item_code = fields.Char(required=True)
    provider_item_id = fields.Char(
        string="Provider Item ID",
        help="The provider's own id for the item (DigiTax), which its sales reference.")
    registered_at = fields.Datetime(default=fields.Datetime.now)
    state = fields.Selection([("registered", "Registered"), ("failed", "Failed")],
                             default="registered", required=True)
    last_error = fields.Text()

    _product_device_unique = models.Constraint(
        "UNIQUE(product_tmpl_id, device_id)",
        "A product is registered once per device.")
