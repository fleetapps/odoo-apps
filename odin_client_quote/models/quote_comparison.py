from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class QuoteComparison(models.Model):
    _name = "odin.quote.comparison"
    _description = "Cover comparison across quotations"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _rec_name = "name"

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: _("New"))
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, tracking=True)
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    validity_date = fields.Date(string="Valid until")
    user_id = fields.Many2one(
        "res.users", string="Prepared by", default=lambda self: self.env.user, tracking=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True
    )
    state = fields.Selection(
        [("draft", "Draft"), ("sent", "Sent")], default="draft", tracking=True
    )

    title = fields.Char(
        string="Subject",
        help="Printed above the quotation number, e.g. 'Medical Insurance'.",
    )
    intro = fields.Char(
        string="Opening line",
        default=lambda self: _("Please review the available cover options and premiums below."),
        help="One sentence telling the customer what to do with this document.",
    )
    note = fields.Html(string="Terms", sanitize_attributes=False)

    plan_ids = fields.One2many(
        "odin.quote.comparison.plan", "comparison_id", string="Plans", copy=True
    )
    detail_ids = fields.One2many(
        "odin.quote.comparison.detail", "comparison_id", string="Details", copy=True
    )
    instalment_plan_ids = fields.Many2many(
        "odin.quote.instalment.plan", string="Instalment options"
    )

    _name_uniq = models.Constraint(
        "UNIQUE (name, company_id)", "A comparison with this reference already exists."
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "odin.quote.comparison"
                ) or _("New")
        return super().create(vals_list)

    @api.constrains("plan_ids", "partner_id")
    def _check_plans_match(self):
        for comparison in self:
            orders = comparison.plan_ids.order_id
            wrong = orders.filtered(lambda o: o.partner_id != comparison.partner_id)
            if wrong:
                raise ValidationError(
                    _(
                        "These quotations are for a different customer: %s. A "
                        "comparison puts one customer's options side by side.",
                        ", ".join(wrong.mapped("name")),
                    )
                )

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id and self.plan_ids.order_id.filtered(
            lambda o: o.partner_id != self.partner_id
        ):
            self.plan_ids = [fields.Command.clear()]

    # -- the printed grid -------------------------------------------------

    def _matrix(self):
        """The comparison as the report prints it: columns, then rows.

        One column per plan, one row per benefit. Limits are grouped first
        (what each plan pays out), then premiums (what it costs), then the
        totals and any instalment options.
        """
        self.ensure_one()
        plans = self.plan_ids.sorted(lambda plan: (plan.sequence, plan.id))
        if not plans:
            return {"plans": plans, "rows": []}

        columns = [
            (plan, plan.order_id._client_sections(), plan.order_id._client_amounts())
            for plan in plans
        ]

        keys, labels = [], {}
        for _plan, sections, _amounts in columns:
            for section in sections:
                if section["included"] and section["key"] not in labels:
                    keys.append(section["key"])
                    labels[section["key"]] = section["label"]

        def cells(getter):
            return [getter(*column) for column in columns]

        def find(sections, key):
            return next((s for s in sections if s["key"] == key), None)

        rows = []
        limit_rows = []
        for key in keys:
            limits = cells(
                lambda _p, sections, _a, key=key: (find(sections, key) or {}).get("limit") or 0.0
            )
            if any(limits):
                limit_rows.append({
                    "kind": "limit",
                    "label": labels[key],
                    "cells": limits,
                })
        if limit_rows:
            rows.append({"kind": "group", "label": _("What each plan covers you for"), "cells": []})
            rows.extend(limit_rows)

        rows.append({"kind": "group", "label": _("What each plan costs"), "cells": []})
        for key in keys:
            rows.append({
                "kind": "premium",
                "label": labels[key],
                "cells": cells(
                    lambda _p, sections, _a, key=key: (find(sections, key) or {}).get("amount") or 0.0
                ),
            })
        rows.append({
            "kind": "premium",
            "label": _("Taxes and levies"),
            "cells": cells(lambda _p, _s, amounts: amounts["tax"]),
        })
        rows.append({
            "kind": "total",
            "label": _("Total premium"),
            "cells": cells(lambda _p, _s, amounts: amounts["total"]),
        })
        if self.instalment_plan_ids:
            rows.append({"kind": "group", "label": _("Or pay monthly"), "cells": []})
            for instalment in self.instalment_plan_ids:
                rows.append({
                    "kind": "instalment",
                    "label": instalment.display_name,
                    "cells": cells(
                        lambda _p, _s, amounts, plan=instalment: plan.schedule(
                            amounts["total"]
                        )["instalment"]
                    ),
                })

        stripe = False
        for row in rows:
            if row["kind"] == "group":
                stripe = False
                continue
            row["alt"] = stripe
            stripe = not stripe
        return {"plans": plans, "rows": rows}

    def _detail_rows(self, columns=4):
        """The details strip, padded into rows of ``columns`` equal cells."""
        self.ensure_one()
        details = list(self.detail_ids)
        if not details:
            return []
        padding = (columns - len(details) % columns) % columns
        details += [None] * padding
        return [details[i:i + columns] for i in range(0, len(details), columns)]

    # -- actions ----------------------------------------------------------

    def action_mark_sent(self):
        self.filtered(lambda c: c.state == "draft").state = "sent"

    @api.model
    def action_from_orders(self):
        """Build a comparison from the quotations selected in the list view."""
        orders = self.env["sale.order"].browse(self.env.context.get("active_ids", []))
        if len(orders) < 2:
            raise UserError(
                _("Pick at least two quotations to compare.")
            )
        if len(orders.partner_id) > 1:
            raise UserError(
                _("These quotations are for different customers, so there is "
                  "nothing to compare. Pick the options offered to one customer.")
            )
        comparison = self.create({
            "partner_id": orders.partner_id.id,
            "company_id": orders[0].company_id.id,
            "note": orders[0].note,
            "plan_ids": [
                fields.Command.create({
                    "order_id": order.id,
                    "sequence": index * 10,
                })
                for index, order in enumerate(orders.sorted("id"))
            ],
            "detail_ids": [
                fields.Command.create({"label": detail["label"], "value": detail["value"]})
                for detail in orders.sorted("id")[0]._client_details()
            ],
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "odin.quote.comparison",
            "res_id": comparison.id,
            "view_mode": "form",
        }


class QuoteComparisonPlan(models.Model):
    _name = "odin.quote.comparison.plan"
    _description = "One plan compared on a cover comparison"
    _order = "sequence, id"

    comparison_id = fields.Many2one(
        "odin.quote.comparison", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    order_id = fields.Many2one(
        "sale.order", string="Quotation", required=True, ondelete="restrict",
        help="The figures in this column are read from this quotation. Change "
        "the quotation and the comparison follows.",
    )
    name = fields.Char(
        string="Column heading", compute="_compute_name", store=True, readonly=False,
        help="Defaults to the quotation template, which is usually the plan name.",
    )
    subtitle = fields.Char(
        string="Second line", help="Printed under the heading, e.g. 'Plan 1'."
    )
    highlight = fields.Boolean(
        string="Recommended",
        help="Marks this column as the one being recommended.",
    )
    currency_id = fields.Many2one(related="order_id.currency_id", readonly=True)
    amount_total = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Total premium"
    )

    @api.depends("order_id")
    def _compute_name(self):
        for plan in self:
            plan.name = (
                plan.order_id.sale_order_template_id.name
                or plan.order_id.name
                or ""
            )

    @api.depends("order_id.amount_total", "order_id.order_line.price_total")
    def _compute_amounts(self):
        for plan in self:
            plan.amount_total = (
                plan.order_id._client_amounts()["total"] if plan.order_id else 0.0
            )


class QuoteComparisonDetail(models.Model):
    _name = "odin.quote.comparison.detail"
    _description = "A fact printed in a comparison's details strip"
    _order = "sequence, id"

    comparison_id = fields.Many2one(
        "odin.quote.comparison", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    label = fields.Char(required=True)
    value = fields.Char()
