from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.safe_eval import safe_eval

AREAS = [
    ("sales", "Sales"),
    ("export", "Export"),
    ("purchases", "Purchases"),
    ("stock", "Stock"),
    ("internal", "Internal"),
]


class DocumentType(models.Model):
    """One business document: what it is for, the record it is printed from,
    its report, and the terms printed on it. The Documents app shows one card
    per document and opens the records it is printed from."""

    _name = "odin.document.type"
    _description = "Business document"
    _order = "area_sequence, sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="The key the document's template reads its terms by.")
    area = fields.Selection(AREAS, required=True, default="sales")
    area_sequence = fields.Integer(compute="_compute_area_sequence", store=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Char(translate=True, help="What the document is for, on its card.")
    printed_from = fields.Char(translate=True, help="Where to print it from, on its card.")
    icon = fields.Char(default="fa-file-text-o", help="Font Awesome icon on the card, e.g. fa-truck.")
    report_id = fields.Many2one("ir.actions.report", string="Report", ondelete="set null")
    res_model = fields.Char(string="Printed from", help="The model of the records the document is printed from.")
    record_domain = fields.Char(default="[]", help="Which of those records the card opens.")
    record_action = fields.Char(help="The action the card opens (its XML id), for the usual list of those records.")
    can_create = fields.Boolean(help="The document is written here (a memo), not printed from another record.")
    terms = fields.Html(
        string="Terms & conditions", sanitize_attributes=False,
        help="Printed at the foot of the document. {company} is replaced by the company's name.")
    acknowledgement = fields.Html(
        help="The statement printed above the signature boxes, e.g. goods received in good condition.")
    record_count = fields.Integer(compute="_compute_counts", string="Records")
    issued_count = fields.Integer(compute="_compute_counts", string="Issued")

    _code_unique = models.Constraint("UNIQUE(code)", "Each document has its own code.")

    @api.depends("area")
    def _compute_area_sequence(self):
        order = {key: index for index, (key, _label) in enumerate(AREAS)}
        for doc in self:
            doc.area_sequence = order.get(doc.area, 99)

    def _compute_counts(self):
        issued = dict(self.env["odin.document.log"].sudo()._read_group(
            [("doc_type_id", "in", self.ids)], ["doc_type_id"], ["__count"]))
        for doc in self:
            doc.issued_count = issued.get(doc, 0)
            doc.record_count = doc._records_count()

    # ------------------------------------------------------------ helpers
    @api.model
    def _get(self, code):
        return self.with_context(active_test=False).search([("code", "=", code)], limit=1)

    def _render_text(self, html, company):
        if not html:
            return Markup("")
        return Markup(html).replace("{company}", company.name or "")

    def _terms_for(self, company):
        return self._render_text(self[:1].terms, company)

    def _acknowledgement_for(self, company):
        return self._render_text(self[:1].acknowledgement, company)

    def _records_domain(self):
        self.ensure_one()
        return safe_eval(self.record_domain or "[]", {"uid": self.env.uid})

    def _records_count(self):
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            return 0
        try:
            return self.env[self.res_model].search_count(self._records_domain())
        except AccessError:
            return 0

    @api.model
    def _invoice_reports(self):
        """The invoice reports, the most particular first: an export invoice
        takes the commercial invoice before the sales invoice."""
        reports = self.env["ir.actions.report"]
        for code in ("commercial_invoice", "credit_note", "sales_invoice"):
            reports |= self.sudo()._get(code).report_id
        return reports

    # ------------------------------------------------------------ actions
    def action_open_records(self):
        """The records the document is printed from, in their usual list."""
        self.ensure_one()
        action = False
        if self.record_action:
            target = self.env.ref(self.record_action, raise_if_not_found=False)
            if target and target._name == "ir.actions.server":
                # e.g. Physical Inventory: the server action builds its own view.
                return self.env["ir.actions.actions"]._for_xml_id(self.record_action)
            if target and target._name == "ir.actions.act_window":
                action = self.env["ir.actions.actions"]._for_xml_id(self.record_action)
                action["context"] = self._without_default_filters(action.get("context"))
        if not action:
            action = {"type": "ir.actions.act_window", "res_model": self.res_model,
                      "views": [(False, "list"), (False, "form")]}
        action.update({
            "name": self.name,
            "domain": self._records_domain(),
            "target": "current",
            "help": Markup("<p class='o_view_nocontent_smiling_face'>%s</p><p>%s</p>") % (
                _("Nothing to print %s from yet", self.name),
                _("Open a record, then Print > %s.", self.name)),
        })
        return action

    def _without_default_filters(self, context):
        """The list's own defaults (an invoice stays an invoice) without its
        default filters: the card shows everyone's records, not only mine."""
        if not context or not isinstance(context, str):
            context = context or {}
        else:
            try:
                context = safe_eval(context, {"uid": self.env.uid, "allowed_company_ids": self.env.companies.ids,
                                              "active_id": False, "active_ids": [], "context": {}})
            except Exception:  # noqa: BLE001 - an unreadable context is kept as Odoo wrote it
                return context
        return {key: value for key, value in dict(context).items() if not key.startswith("search_default_")}

    def action_new(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self.res_model, "view_mode": "form",
                "views": [(False, "form")], "target": "current", "name": self.name}

    def action_open_issued(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("odin_documents.action_document_log")
        action["domain"] = [("doc_type_id", "=", self.id)]
        action["name"] = _("%s: issued", self.name)
        return action


class DocumentLog(models.Model):
    """The register of issued documents: each PDF printed or sent, of which
    document, for which record and customer or supplier, by whom and when."""

    _name = "odin.document.log"
    _description = "Issued business document"
    _order = "date desc, id desc"
    _rec_name = "name"

    doc_type_id = fields.Many2one("odin.document.type", string="Document", required=True, ondelete="cascade",
                                  index=True)
    area = fields.Selection(related="doc_type_id.area", store=True)
    name = fields.Char("Reference", readonly=True)
    res_model = fields.Char(readonly=True, index=True)
    res_id = fields.Many2oneReference(model_field="res_model", readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True, index=True)
    amount = fields.Monetary(currency_field="currency_id", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    user_id = fields.Many2one("res.users", string="Printed by", default=lambda self: self.env.user, readonly=True)
    date = fields.Datetime(default=fields.Datetime.now, readonly=True, index=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, readonly=True)

    @api.model
    def _log_print(self, report, res_ids):
        doc_type = self.env["odin.document.type"].sudo().search([("report_id", "=", report.id)], limit=1)
        if not doc_type or not report.model or report.model not in self.env:
            return
        records = self.env[report.model].sudo().browse(res_ids).exists()
        if report.model == "stock.quant" and records:
            # A count sheet is one document, however many stock lines it lists.
            self.sudo().create({"doc_type_id": doc_type.id, "name": records._doc_sheet_ref(),
                                "res_model": False, "res_id": False,
                                "company_id": records[:1].company_id.id or self.env.company.id})
            return
        values = []
        for record in records:
            partner = record.partner_id if "partner_id" in record._fields else False
            if partner and partner._name != "res.partner":
                partner = False
            amount = record.amount_total if "amount_total" in record._fields else 0.0
            currency = record.currency_id if "currency_id" in record._fields else False
            company = record.company_id if "company_id" in record._fields else False
            values.append({
                "doc_type_id": doc_type.id, "name": record.display_name, "res_model": record._name,
                "res_id": record.id, "partner_id": partner.id if partner else False,
                "amount": amount if currency else 0.0, "currency_id": currency.id if currency else False,
                "company_id": (company or self.env.company).id,
            })
        if values:
            self.sudo().create(values)

    def action_open_record(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return False
        return {"type": "ir.actions.act_window", "res_model": self.res_model, "res_id": self.res_id,
                "view_mode": "form", "views": [(False, "form")], "target": "current"}
