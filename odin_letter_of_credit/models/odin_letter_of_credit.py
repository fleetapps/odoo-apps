# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# Who we are in the credit. It decides which side the documents are checked on
# and which document the utilisation settles against.
DIRECTION = [
    # We are the applicant: our bank issues in favour of a supplier, and we pay.
    ("import", "Import — we are the applicant"),
    # We are the beneficiary: the employer's bank issues in our favour, we present.
    ("export", "Export — we are the beneficiary"),
]

LC_TYPE = [
    ("sight", "Sight"),
    ("usance", "Usance / Deferred"),
    ("standby", "Standby"),
    ("revolving", "Revolving"),
    ("transferable", "Transferable"),
]


class OdinLetterOfCredit(models.Model):
    _name = "odin.letter.of.credit"
    _inherit = ["mail.thread", "mail.activity.mixin", "analytic.mixin"]
    _description = "Letter of Credit"
    _order = "date_expiry, id desc"

    name = fields.Char(
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: self.env._("New"),
        index=True,
    )
    lc_reference = fields.Char(
        string="Bank Reference",
        tracking=True,
        index="btree_not_null",
        help="The credit number the issuing bank uses. This is what appears on "
        "every document and every swift message.",
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
        tracking=True,
    )
    direction = fields.Selection(DIRECTION, required=True, default="import", tracking=True)
    lc_type = fields.Selection(LC_TYPE, required=True, default="sight", tracking=True)
    tenor_days = fields.Integer(
        string="Tenor (days)",
        tracking=True,
        help="Days after presentation or shipment that payment falls due, on a "
        "usance credit.",
    )

    partner_id = fields.Many2one(
        "res.partner",
        string="Counterparty",
        required=True,
        index=True,
        tracking=True,
        help="The beneficiary on an import credit, the applicant on an export one.",
    )
    issuing_bank_id = fields.Many2one("res.partner", string="Issuing Bank", tracking=True)
    advising_bank_id = fields.Many2one("res.partner", string="Advising Bank", tracking=True)
    confirming_bank_id = fields.Many2one(
        "res.partner",
        string="Confirming Bank",
        tracking=True,
        help="Set where the credit is confirmed. A confirmed credit carries the "
        "confirming bank's undertaking as well as the issuer's.",
    )

    # ── Amount and tolerance ────────────────────────────────────────────────
    amount = fields.Monetary(required=True, tracking=True)
    tolerance_percent = fields.Float(
        string="Tolerance (%)",
        digits=(16, 2),
        tracking=True,
        help="The plus tolerance the credit allows, e.g. 5 for '5% more or less'.",
    )
    amount_max = fields.Monetary(
        string="Maximum Drawable",
        compute="_compute_amount_max",
        store=True,
    )
    amount_utilised = fields.Monetary(compute="_compute_utilisation", store=True)
    amount_available = fields.Monetary(compute="_compute_utilisation", store=True)
    utilisation_status = fields.Selection(
        [("none", "Undrawn"), ("partial", "Partially Drawn"), ("full", "Fully Drawn")],
        compute="_compute_utilisation",
        store=True,
    )

    # ── Dates ───────────────────────────────────────────────────────────────
    date_application = fields.Date(default=fields.Date.context_today, tracking=True)
    date_issue = fields.Date(tracking=True)
    date_expiry = fields.Date(required=True, tracking=True, index=True)
    date_latest_shipment = fields.Date(tracking=True)
    presentation_days = fields.Integer(
        default=21,
        help="Days after shipment within which documents must be presented. "
        "UCP 600 defaults to 21 where the credit is silent.",
    )
    place_of_expiry = fields.Char(tracking=True)
    days_to_expiry = fields.Integer(compute="_compute_expiry", search="_search_days_to_expiry")
    expiry_status = fields.Selection(
        [
            ("open", "Open"),
            ("soon", "Expiring Soon"),
            ("expired", "Expired"),
        ],
        compute="_compute_expiry",
        search="_search_expiry_status",
    )

    # ── Terms ───────────────────────────────────────────────────────────────
    incoterm_id = fields.Many2one("account.incoterms", string="Incoterm")
    partial_shipment = fields.Boolean(string="Partial Shipment Allowed", default=True)
    transhipment = fields.Boolean(string="Transhipment Allowed", default=True)
    goods_description = fields.Text()

    # ── Links ───────────────────────────────────────────────────────────────
    project_id = fields.Many2one("project.project", index=True, tracking=True)
    purchase_order_ids = fields.Many2many(
        "purchase.order",
        string="Purchase Orders",
        help="The orders this credit covers. Used to warn when a bill is paid "
        "outside the credit.",
    )
    document_ids = fields.One2many("odin.lc.document", "lc_id", string="Required Documents")
    amendment_ids = fields.One2many("odin.lc.amendment", "lc_id", string="Amendments")
    utilisation_ids = fields.One2many("odin.lc.utilisation", "lc_id", string="Utilisations")
    charge_ids = fields.One2many("odin.lc.charge", "lc_id", string="Bank Charges")
    move_ids = fields.One2many("account.move", "odin_lc_id", string="Documents")

    utilisation_count = fields.Integer(compute="_compute_counts")
    amendment_count = fields.Integer(compute="_compute_counts")
    move_count = fields.Integer(compute="_compute_counts")
    charge_total = fields.Monetary(compute="_compute_counts", string="Total Bank Charges")

    # ── Document control ────────────────────────────────────────────────────
    documents_required_count = fields.Integer(compute="_compute_documents", store=True)
    documents_received_count = fields.Integer(compute="_compute_documents", store=True)
    documents_complete = fields.Boolean(
        compute="_compute_documents",
        store=True,
        help="True once every document the credit requires has been received. "
        "Payment against this credit is blocked until then.",
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("applied", "Applied For"),
            ("issued", "Issued"),
            ("advised", "Advised"),
            ("closed", "Closed"),
            ("expired", "Expired"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    note = fields.Html(sanitize_attributes=True)

    _name_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)",
        "A letter of credit with this reference already exists.",
    )
    _amount_positive = models.Constraint(
        "CHECK(amount > 0)", "The credit amount must be greater than zero."
    )
    _tolerance_sane = models.Constraint(
        "CHECK(tolerance_percent >= 0 AND tolerance_percent <= 100)",
        "Tolerance must be between 0 and 100 percent.",
    )

    # ── Computes ────────────────────────────────────────────────────────────
    @api.depends("amount", "tolerance_percent")
    def _compute_amount_max(self):
        for lc in self:
            lc.amount_max = lc.currency_id.round(
                lc.amount * (1.0 + (lc.tolerance_percent or 0.0) / 100.0)
            )

    @api.depends(
        "amount_max",
        "utilisation_ids.amount",
        "utilisation_ids.state",
    )
    def _compute_utilisation(self):
        for lc in self:
            drawn = sum(
                u.amount
                for u in lc.utilisation_ids
                if u.state in ("accepted", "paid")
            )
            lc.amount_utilised = lc.currency_id.round(drawn)
            lc.amount_available = lc.currency_id.round(
                max(0.0, lc.amount_max - drawn)
            )
            if lc.currency_id.is_zero(drawn):
                lc.utilisation_status = "none"
            elif lc.currency_id.compare_amounts(drawn, lc.amount_max) >= 0:
                lc.utilisation_status = "full"
            else:
                lc.utilisation_status = "partial"

    @api.depends("document_ids.is_required", "document_ids.is_received")
    def _compute_documents(self):
        for lc in self:
            required = lc.document_ids.filtered("is_required")
            received = required.filtered("is_received")
            lc.documents_required_count = len(required)
            lc.documents_received_count = len(received)
            # An LC with no documents listed is not "complete" by accident —
            # it is unconfigured, and paying against it should still be blocked.
            lc.documents_complete = bool(required) and len(received) == len(required)

    @api.depends("date_expiry", "state")
    def _compute_expiry(self):
        today = fields.Date.context_today(self)
        for lc in self:
            if not lc.date_expiry:
                lc.days_to_expiry = 0
                lc.expiry_status = "open"
                continue
            days = (lc.date_expiry - today).days
            lc.days_to_expiry = days
            if lc.state in ("closed", "cancelled"):
                lc.expiry_status = "open"
            elif days < 0:
                lc.expiry_status = "expired"
            elif days <= 30:
                lc.expiry_status = "soon"
            else:
                lc.expiry_status = "open"

    def _search_days_to_expiry(self, operator, value):
        # "days to expiry <op> N" becomes a date comparison, with the operator
        # INVERTED: a later expiry date means more days remaining, not fewer.
        inverted = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "=", "!=": "!="}
        boundary = fields.Date.context_today(self) + timedelta(days=value)
        return [("date_expiry", inverted.get(operator, operator), boundary)]

    def _search_expiry_status(self, operator, value):
        today = fields.Date.context_today(self)
        if operator not in ("=", "!="):
            raise UserError(
                self.env._("Expiry status can only be searched for equality.")
            )
        domains = {
            "expired": [("date_expiry", "<", today)],
            "soon": [
                ("date_expiry", ">=", today),
                ("date_expiry", "<=", today + timedelta(days=30)),
            ],
            "open": [("date_expiry", ">", today + timedelta(days=30))],
        }
        domain = domains.get(value, [])
        if operator == "!=":
            return ["!"] + domain
        return domain

    @api.depends(
        "utilisation_ids", "amendment_ids", "move_ids", "charge_ids.amount"
    )
    def _compute_counts(self):
        for lc in self:
            lc.utilisation_count = len(lc.utilisation_ids)
            lc.amendment_count = len(lc.amendment_ids)
            lc.move_count = len(lc.move_ids)
            lc.charge_total = sum(lc.charge_ids.mapped("amount"))

    # ── Constraints ─────────────────────────────────────────────────────────
    @api.constrains("date_expiry", "date_issue", "date_latest_shipment")
    def _check_dates(self):
        for lc in self:
            if lc.date_issue and lc.date_expiry < lc.date_issue:
                raise ValidationError(
                    self.env._("A credit cannot expire before it was issued.")
                )
            if (
                lc.date_latest_shipment
                and lc.date_expiry
                and lc.date_latest_shipment > lc.date_expiry
            ):
                raise ValidationError(
                    self.env._(
                        "The latest shipment date falls after the credit expires, "
                        "so a compliant presentation would be impossible."
                    )
                )

    @api.constrains("lc_type", "tenor_days")
    def _check_tenor(self):
        for lc in self:
            if lc.lc_type == "usance" and lc.tenor_days <= 0:
                raise ValidationError(
                    self.env._("A usance credit needs a tenor in days.")
                )

    # ── CRUD ────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", self.env._("New")) == self.env._("New"):
                company_id = vals.get("company_id", self.env.company.id)
                vals["name"] = self.env["ir.sequence"].with_company(
                    company_id
                ).next_by_code("odin.letter.of.credit") or self.env._("New")
        return super().create(vals_list)

    @api.onchange("project_id")
    def _onchange_project_id(self):
        if self.project_id and not self.analytic_distribution:
            if self.project_id.account_id:
                self.analytic_distribution = {str(self.project_id.account_id.id): 100.0}

    # ── Actions ─────────────────────────────────────────────────────────────
    def action_apply(self):
        for lc in self:
            if lc.state != "draft":
                raise UserError(self.env._("Only a draft credit can be applied for."))
            if not lc.document_ids.filtered("is_required"):
                raise UserError(
                    self.env._(
                        "List the documents %(name)s calls for before applying. "
                        "The document set is what the credit actually pays against.",
                        name=lc.name,
                    )
                )
        self.write({"state": "applied"})
        return True

    def action_issue(self):
        for lc in self:
            if lc.state not in ("draft", "applied"):
                raise UserError(self.env._("This credit has already been issued."))
            if not lc.lc_reference:
                raise UserError(
                    self.env._(
                        "Record the bank's credit reference for %(name)s before "
                        "marking it issued.",
                        name=lc.name,
                    )
                )
        self.write(
            {"state": "issued", "date_issue": fields.Date.context_today(self)}
        )
        return True

    def action_advise(self):
        for lc in self:
            if lc.state != "issued":
                raise UserError(
                    self.env._("Only an issued credit can be advised.")
                )
        self.write({"state": "advised"})
        return True

    def action_close(self):
        for lc in self:
            open_utilisations = lc.utilisation_ids.filtered(
                lambda u: u.state in ("draft", "presented", "discrepant", "accepted")
            )
            if open_utilisations:
                raise UserError(
                    self.env._(
                        "%(count)s presentation(s) under %(name)s are still open.",
                        count=len(open_utilisations),
                        name=lc.name,
                    )
                )
        self.write({"state": "closed"})
        return True

    def action_cancel(self):
        for lc in self:
            if lc.utilisation_ids.filtered(lambda u: u.state == "paid"):
                raise UserError(
                    self.env._(
                        "%(name)s has been drawn against and cannot be cancelled. "
                        "Close it instead.",
                        name=lc.name,
                    )
                )
        self.write({"state": "cancelled"})
        return True

    def action_draft(self):
        self.write({"state": "draft"})
        return True

    def action_open_amend_wizard(self):
        self.ensure_one()
        if self.state not in ("issued", "advised"):
            raise UserError(
                self.env._("Only an issued credit can be amended.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Amend Credit"),
            "res_model": "odin.lc.amend.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_lc_id": self.id},
        }

    def _action_related(self, name, model, domain_field):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": "list,form",
            "domain": [(domain_field, "=", self.id)],
            "context": {f"default_{domain_field}": self.id},
        }

    def action_view_utilisations(self):
        return self._action_related(
            self.env._("Utilisations"), "odin.lc.utilisation", "lc_id"
        )

    def action_view_amendments(self):
        return self._action_related(
            self.env._("Amendments"), "odin.lc.amendment", "lc_id"
        )

    def action_view_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Documents"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("odin_lc_id", "=", self.id)],
        }

    # ── Cron ────────────────────────────────────────────────────────────────
    @api.model
    def _cron_expiry_watch(self):
        """Expire what has expired, and warn before the rest does."""
        today = fields.Date.context_today(self)
        live = self.search([("state", "in", ("issued", "advised"))])

        expired = live.filtered(lambda lc: lc.date_expiry and lc.date_expiry < today)
        if expired:
            expired.write({"state": "expired"})
            for lc in expired:
                lc.message_post(
                    body=self.env._("The credit expired on %(date)s.", date=lc.date_expiry)
                )

        for lc in live - expired:
            horizon = today + timedelta(days=30)
            if lc.date_expiry and lc.date_expiry <= horizon:
                lc._warn_once(
                    self.env._(
                        "%(name)s expires on %(date)s with %(outstanding)s document(s) "
                        "still outstanding.",
                        name=lc.name,
                        date=lc.date_expiry,
                        outstanding=lc.documents_required_count
                        - lc.documents_received_count,
                    )
                )
            if (
                lc.date_latest_shipment
                and lc.date_latest_shipment <= today + timedelta(days=14)
                and lc.date_latest_shipment >= today
            ):
                lc._warn_once(
                    self.env._(
                        "Latest shipment under %(name)s is %(date)s.",
                        name=lc.name,
                        date=lc.date_latest_shipment,
                    )
                )
        return True

    def _warn_once(self, body):
        self.ensure_one()
        summary = body[:200]
        already = self.env["mail.activity"].search_count(
            [
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("summary", "=", summary),
            ]
        )
        if already:
            return
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            summary=summary,
            user_id=self.create_uid.id or self.env.uid,
        )
