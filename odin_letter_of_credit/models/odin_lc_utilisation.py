# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class OdinLcUtilisation(models.Model):
    """A presentation of documents, and the drawing that follows it.

    Discrepant is a first-class state rather than a note, because a discrepant
    presentation is the normal case in trade finance and the whole question is
    whether the applicant waives the discrepancy or not.
    """

    _name = "odin.lc.utilisation"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Letter of Credit Utilisation"
    _order = "date_presentation desc, id desc"

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: self.env._("New"), index=True,
    )
    lc_id = fields.Many2one(
        "odin.letter.of.credit", required=True, ondelete="restrict", index=True,
        tracking=True,
    )
    company_id = fields.Many2one(related="lc_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="lc_id.currency_id", store=True)
    partner_id = fields.Many2one(related="lc_id.partner_id", store=True)
    project_id = fields.Many2one(related="lc_id.project_id", store=True)
    direction = fields.Selection(related="lc_id.direction", store=True)

    date_presentation = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True
    )
    date_shipment = fields.Date(
        tracking=True,
        help="Shipment date on the transport document. Drives the presentation "
        "deadline and is checked against the credit's latest shipment date.",
    )
    date_maturity = fields.Date(
        compute="_compute_date_maturity",
        store=True,
        readonly=False,
        tracking=True,
        help="When payment falls due. Computed from the tenor on a usance credit.",
    )
    amount = fields.Monetary(required=True, tracking=True)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("presented", "Presented"),
            ("discrepant", "Discrepant"),
            ("accepted", "Accepted"),
            ("paid", "Paid"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    discrepancy_note = fields.Text(tracking=True)
    move_id = fields.Many2one(
        "account.move",
        string="Invoice / Bill",
        tracking=True,
        index="btree_not_null",
        help="The document this drawing settles.",
    )
    note = fields.Text()

    _name_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)",
        "A utilisation with this reference already exists.",
    )
    _amount_positive = models.Constraint(
        "CHECK(amount > 0)", "A drawing must be greater than zero."
    )

    @api.depends("lc_id.lc_type", "lc_id.tenor_days", "date_presentation", "date_shipment")
    def _compute_date_maturity(self):
        for util in self:
            lc = util.lc_id
            if lc.lc_type == "usance" and lc.tenor_days:
                base = util.date_shipment or util.date_presentation
                util.date_maturity = base + timedelta(days=lc.tenor_days) if base else False
            else:
                util.date_maturity = util.date_presentation

    @api.constrains("amount", "lc_id", "state")
    def _check_within_credit(self):
        for util in self:
            if util.state not in ("accepted", "paid"):
                continue
            lc = util.lc_id
            others = lc.utilisation_ids.filtered(
                lambda u, cur=util: u.id != cur.id and u.state in ("accepted", "paid")
            )
            total = sum(others.mapped("amount")) + util.amount
            if lc.currency_id.compare_amounts(total, lc.amount_max) > 0:
                raise ValidationError(
                    self.env._(
                        "Drawing %(amount)s would take total utilisation to "
                        "%(total)s against a maximum of %(max)s on %(lc)s.",
                        amount=util.amount,
                        total=total,
                        max=lc.amount_max,
                        lc=lc.name,
                    )
                )

    @api.constrains("date_shipment", "lc_id")
    def _check_shipment_date(self):
        for util in self:
            lc = util.lc_id
            if (
                util.date_shipment
                and lc.date_latest_shipment
                and util.date_shipment > lc.date_latest_shipment
            ):
                raise ValidationError(
                    self.env._(
                        "Shipment on %(shipped)s is later than the latest shipment "
                        "date of %(latest)s. That is a discrepancy — record the "
                        "presentation as discrepant rather than changing the date.",
                        shipped=util.date_shipment,
                        latest=lc.date_latest_shipment,
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", self.env._("New")) == self.env._("New"):
                company_id = vals.get("company_id") or self.env.company.id
                vals["name"] = self.env["ir.sequence"].with_company(
                    company_id
                ).next_by_code("odin.lc.utilisation") or self.env._("New")
        return super().create(vals_list)

    # ── Workflow ────────────────────────────────────────────────────────────
    def action_present(self):
        for util in self:
            if util.state != "draft":
                raise UserError(self.env._("This presentation has already been made."))
            lc = util.lc_id
            if lc.state not in ("issued", "advised"):
                raise UserError(
                    self.env._(
                        "%(lc)s is not live, so nothing can be presented under it.",
                        lc=lc.name,
                    )
                )
            util._check_presentation_period()
        self.write({"state": "presented"})
        return True

    def _check_presentation_period(self):
        """UCP 600 art. 14(c): present within the stated period, and before expiry."""
        self.ensure_one()
        lc = self.lc_id
        if lc.date_expiry and self.date_presentation > lc.date_expiry:
            raise UserError(
                self.env._(
                    "Presentation on %(date)s is after the credit expired on "
                    "%(expiry)s.",
                    date=self.date_presentation,
                    expiry=lc.date_expiry,
                )
            )
        if self.date_shipment and lc.presentation_days:
            deadline = self.date_shipment + timedelta(days=lc.presentation_days)
            if self.date_presentation > deadline:
                raise UserError(
                    self.env._(
                        "Documents must be presented within %(days)s days of "
                        "shipment, so by %(deadline)s. Mark the presentation "
                        "discrepant if it is late.",
                        days=lc.presentation_days,
                        deadline=deadline,
                    )
                )

    def action_mark_discrepant(self):
        for util in self:
            if util.state not in ("draft", "presented"):
                raise UserError(
                    self.env._("Only a presentation can be marked discrepant.")
                )
            if not util.discrepancy_note:
                raise UserError(
                    self.env._(
                        "List the discrepancies on %(name)s. A bank that refuses "
                        "must state every one of them, and so should we.",
                        name=util.name,
                    )
                )
        self.write({"state": "discrepant"})
        return True

    def action_accept(self):
        """Documents taken up — either compliant, or discrepancies waived."""
        for util in self:
            if util.state not in ("presented", "discrepant"):
                raise UserError(
                    self.env._("Only a presented drawing can be accepted.")
                )
        self.write({"state": "accepted"})
        return True

    def action_reject(self):
        for util in self:
            if util.state not in ("presented", "discrepant"):
                raise UserError(
                    self.env._("Only a presented drawing can be refused.")
                )
        self.write({"state": "rejected"})
        return True

    def action_mark_paid(self):
        for util in self:
            if util.state != "accepted":
                raise UserError(
                    self.env._(
                        "Documents under %(name)s have not been accepted, so no "
                        "payment is due under the credit.",
                        name=util.name,
                    )
                )
        self.write({"state": "paid"})
        for util in self:
            util.lc_id.message_post(
                body=self.env._(
                    "Drawing %(name)s paid: %(amount)s. Remaining availability "
                    "%(available)s.",
                    name=util.name,
                    amount=util.amount,
                    available=util.lc_id.amount_available,
                )
            )
        return True

    def action_draft(self):
        for util in self:
            if util.state == "paid":
                raise UserError(
                    self.env._("A paid drawing cannot be reopened.")
                )
        self.write({"state": "draft"})
        return True
