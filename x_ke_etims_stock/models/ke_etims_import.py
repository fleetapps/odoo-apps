# -*- coding: utf-8 -*-
"""Imports customs recorded under our PIN, to link to a product and approve.

KRA's customs system declares each imported item to eTIMS. The trader says
which of their items it is, and approves (code 3) or rejects (code 4) it --
an import left unapproved is a stock KRA thinks arrived and the books do not.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.x_ke_etims.backends import ImportUpdate

APPROVED = "3"
CANCELLED = "4"


class KeEtimsImport(models.Model):
    _name = "ke.etims.import"
    _description = "eTIMS Customs Import"
    _inherit = ["mail.thread"]
    _order = "declaration_date desc, task_code, item_seq"
    _check_company_auto = True

    name = fields.Char(required=True)
    device_id = fields.Many2one("ke.etims.device", required=True, index=True, ondelete="restrict")
    company_id = fields.Many2one(related="device_id.company_id", store=True, index=True)
    provider_ref = fields.Char(required=True, index=True)
    task_code = fields.Char(required=True)
    declaration_date = fields.Date()
    declaration_no = fields.Char(string="Declaration No.")
    item_seq = fields.Integer(string="Item")
    hs_code = fields.Char(string="HS Code")
    origin_country = fields.Char()
    export_country = fields.Char()
    quantity = fields.Float()
    quantity_unit = fields.Char()
    package_quantity = fields.Float()
    packaging_unit = fields.Char()
    gross_weight = fields.Float()
    net_weight = fields.Float()
    supplier_name = fields.Char()
    agent_name = fields.Char()
    invoice_amount = fields.Float(string="Invoice Amount (foreign)")
    invoice_currency = fields.Char()
    exchange_rate = fields.Float()
    kra_status = fields.Char(string="KRA Status",
                             help="KRA's code: 1 unsent, 2 waiting, 3 approved, 4 cancelled.")
    product_id = fields.Many2one("product.product", string="Our Product", tracking=True)
    decision = fields.Selection([("approve", "Approve"), ("reject", "Reject")], tracking=True)
    state = fields.Selection(
        [("new", "To Review"), ("sending", "Sending"), ("approved", "Approved"),
         ("rejected", "Rejected")], default="new", required=True, tracking=True)

    _device_ref_unique = models.Constraint(
        "UNIQUE(device_id, provider_ref)", "This import item was already fetched.")

    @api.model
    def _ke_etims_store(self, device, rows):
        created = self.browse()
        for row in rows:
            ref = row.get("provider_ref")
            if not ref or self.search_count([("device_id", "=", device.id),
                                             ("provider_ref", "=", ref)]):
                continue
            created |= self.create({
                "device_id": device.id,
                "provider_ref": ref,
                "name": row.get("name") or row.get("hs_code") or ref,
                "task_code": row.get("task_code") or ref,
                "declaration_date": row.get("declaration_date"),
                "declaration_no": row.get("declaration_no"),
                "item_seq": row.get("item_seq") or 0,
                "hs_code": row.get("hs_code"),
                "origin_country": row.get("origin_country"),
                "export_country": row.get("export_country"),
                "quantity": float(row.get("quantity") or 0),
                "quantity_unit": row.get("quantity_unit"),
                "package_quantity": float(row.get("package_quantity") or 0),
                "packaging_unit": row.get("packaging_unit"),
                "gross_weight": float(row.get("gross_weight") or 0),
                "net_weight": float(row.get("net_weight") or 0),
                "supplier_name": row.get("supplier_name"),
                "agent_name": row.get("agent_name"),
                "invoice_amount": float(row.get("invoice_amount") or 0),
                "invoice_currency": row.get("invoice_currency"),
                "exchange_rate": float(row.get("exchange_rate") or 0),
                "kra_status": row.get("status"),
            })
        return created

    def _ke_etims_decide(self, decision):
        Document = self.env["ke.etims.document"].sudo()
        for record in self:
            if record.state in ("approved", "rejected"):
                continue
            if decision == "approve" and not record.product_id:
                raise UserError(_("Link %s to one of your products before approving it.",
                                  record.name))
            record.write({"decision": decision, "state": "sending"})
            document = Document._ke_etims_enqueue(record, "import_update")
            document.action_send_now()
        return True

    def action_approve(self):
        return self._ke_etims_decide("approve")

    def action_reject(self):
        return self._ke_etims_decide("reject")

    # ------------------------------------------------- the eTIMS source API

    def _ke_etims_device(self):
        self.ensure_one()
        return self.device_id

    def _ke_etims_validate(self, document):
        self.ensure_one()
        if self.decision == "approve":
            template = self.product_id.product_tmpl_id
            if not template:
                return [_("Link the import to a product first.")]
            if not template._ke_etims_registration(self.device_id) and not \
                    template._ke_etims_register(self.device_id, document=document,
                                                raise_errors=False):
                return [_("%s is not registered with eTIMS.", template.display_name)]
        return []

    def _ke_etims_prepare(self, document):
        self.ensure_one()
        template = self.product_id.product_tmpl_id
        registration = template._ke_etims_registration(self.device_id) if template else None
        return ImportUpdate(
            task_code=self.task_code,
            declaration_date=self.declaration_date,
            item_seq=self.item_seq,
            hs_code=self.hs_code or "",
            item_class_code=template.ke_etims_item_class_id.code or "",
            item_code=(registration.item_code if registration else template.ke_etims_item_code)
            or "",
            status=APPROVED if self.decision == "approve" else CANCELLED,
            operator=self.device_id._ke_etims_operator(),
            provider_ref=self.provider_ref,
        )

    def _ke_etims_on_signed(self, document):
        self.state = "approved" if self.decision == "approve" else "rejected"

    def _ke_etims_on_failed(self, document):
        self.write({"state": "new"})
        self.message_post(body=_("eTIMS refused the decision: %s", document.last_error))
        document._ke_etims_schedule_activity()
