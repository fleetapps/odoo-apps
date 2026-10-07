import base64
import re
from urllib.parse import quote

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
    template_ids = fields.Many2many(
        "sale.order.template",
        string="Plans to compare",
        help="Pick the plans to put side by side. Creating the comparison "
        "creates a quotation per plan for this customer, so there is nothing "
        "to prepare beforehand.",
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

    def action_build_plans(self):
        """Create one quotation per selected template and compare them.

        Without this a salesperson has to build every quotation by hand before
        a comparison has anything to show, which is the wrong way round: the
        plans are the input, the quotations are a by-product.

        Odoo fills a quotation from a template through an onchange, so creating
        the order with ``sale_order_template_id`` set would leave it empty. The
        lines are prepared here with the same ``_prepare_order_line_values``
        the onchange uses, which also carries the benefit name, key and cover
        limit across.
        """
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Choose the customer before building the plans."))
        if not self.template_ids:
            raise UserError(
                _("Pick at least one plan to compare under 'Plans to compare'.")
            )

        existing = self.plan_ids.order_id.sale_order_template_id
        todo = self.template_ids - existing
        if not todo:
            raise UserError(
                _("Every plan selected already has a quotation on this comparison.")
            )

        SaleOrder = self.env["sale.order"]
        Plan = self.env["odin.quote.comparison.plan"]
        created = Plan.browse()
        sequence = max(self.plan_ids.mapped("sequence") or [0])
        for template in todo:
            localised = template.with_context(lang=self.partner_id.lang)
            values = [
                fields.Command.create(line._prepare_order_line_values())
                for line in localised.sale_order_template_line_ids
            ]
            if values:
                # Odoo's own convention: the first line sits at -99 so that
                # resequencing the top of the order does not shuffle the rest.
                values[0][2]["sequence"] = -99
            order = SaleOrder.create({
                "partner_id": self.partner_id.id,
                "company_id": self.company_id.id,
                "user_id": self.user_id.id,
                "sale_order_template_id": template.id,
                "order_line": values,
            })
            sequence += 10
            created |= Plan.create({
                "comparison_id": self.id,
                "order_id": order.id,
                "sequence": sequence,
            })

        # Seed the strip and the terms from the first plan. Read them off the
        # records just created rather than self.plan_ids, which may still hold
        # the pre-create value.
        first = (self.plan_ids | created).sorted(lambda p: (p.sequence, p.id))[:1]
        if not self.detail_ids and first:
            self.detail_ids = [
                fields.Command.create({"label": d["label"], "value": d["value"]})
                for d in first.order_id._client_details()
            ]
        if not self.note and first:
            self.note = first.order_id.note
        return True

    def action_open_plan_orders(self):
        """Open the quotations behind this comparison, to adjust cover."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Quotations in this comparison"),
            "res_model": "sale.order",
            "domain": [("id", "in", self.plan_ids.order_id.ids)],
            "view_mode": "list,form",
        }

    # -- getting it to the customer ---------------------------------------

    def action_print(self):
        """Print the comparison. Available in any state: a broker talks a
        customer through a draft far more often than they send a final one."""
        self.ensure_one()
        return self.env.ref(
            "odin_client_quote.action_report_quote_comparison"
        ).report_action(self)

    def _comparison_pdf_attachment(self):
        """Render the comparison and keep the PDF on the record."""
        self.ensure_one()
        pdf, _ext = self.env["ir.actions.report"]._render_qweb_pdf(
            "odin_client_quote.report_quote_comparison", self.ids
        )
        return self.env["ir.attachment"].create({
            "name": "%s.pdf" % (self.name or "comparison"),
            "type": "binary",
            "datas": base64.b64encode(pdf),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/pdf",
        })

    def _whatsapp_number(self):
        """The customer's number in the digits-only form wa.me expects.

        A Kenyan number is usually stored as 0715152515; wa.me needs it in
        international form, so a leading zero is swapped for the country's
        dialling code rather than sent as-is, which silently opens a chat with
        nobody.

        Odoo 19 merged `mobile` into `phone` on res.partner -- there is no
        `mobile` field any more, on any model.
        """
        self.ensure_one()
        partner = self.partner_id
        raw = partner.phone or ""
        digits = re.sub(r"\D", "", raw)
        if not digits:
            return ""
        code = str(
            partner.country_id.phone_code
            or self.company_id.country_id.phone_code
            or ""
        )
        if raw.strip().startswith("+") or (code and digits.startswith(code)):
            return digits
        if code:
            return code + digits.lstrip("0")
        return digits

    def action_share_whatsapp(self):
        """Open WhatsApp with the comparison attached as a download link.

        The PDF is stored as an attachment with an access token, which is how
        Odoo itself shares documents outside the backend: the link needs no
        login but cannot be guessed. Brokers here share on WhatsApp rather than
        email, so this is the path that actually gets used.
        """
        self.ensure_one()
        if not self.plan_ids:
            raise UserError(_("There is nothing to share until the comparison has plans."))
        attachment = self._comparison_pdf_attachment()
        token = attachment.generate_access_token()[0]
        link = "%s/web/content/%s?access_token=%s&download=true" % (
            self.get_base_url(), attachment.id, token,
        )
        message = _(
            "Hello %(name)s, here is the cover comparison we discussed from "
            "%(company)s. It sets the plans side by side so you can see what "
            "each one covers and what it costs.\n\n%(link)s",
            name=self.partner_id.name or "",
            company=self.company_id.name,
            link=link,
        )
        self.action_mark_sent()
        return {
            "type": "ir.actions.act_url",
            "url": "https://wa.me/%s?text=%s" % (self._whatsapp_number(), quote(message)),
            "target": "new",
        }

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
        string="Column heading", compute="_compute_heading", store=True, readonly=False,
        help="Defaults to the first half of the quotation template's name.",
    )
    subtitle = fields.Char(
        string="Second line", compute="_compute_heading", store=True, readonly=False,
        help="Printed under the heading, e.g. 'Plan 1 (KES 200,000)'.",
    )
    highlight = fields.Boolean(
        string="Recommended",
        help="Marks this column as the one being recommended.",
    )
    currency_id = fields.Many2one(related="order_id.currency_id", readonly=True)
    amount_total = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Total premium"
    )

    @api.depends("order_id", "order_id.sale_order_template_id", "sequence")
    def _compute_heading(self):
        """Split the template name into a heading and a second line.

        Plan names read "APA Jamii Plus - Family Cover (KES 500,000)": the half
        before the dash names the product and the half after distinguishes this
        plan from its siblings, which is exactly the two-line column head the
        report wants. A quotation with no template falls back to "Option N" --
        its order reference means nothing to the customer reading the page.
        """
        for plan in self:
            template = plan.order_id.sale_order_template_id
            if template:
                head, _sep, tail = (template.name or "").partition("\u2013")
                if not _sep:
                    head, _sep, tail = (template.name or "").partition(" - ")
                plan.name = head.strip() or template.name
                plan.subtitle = tail.strip()
            else:
                # Count by sequence rather than by index: during creation the
                # plan is not yet among its own siblings, which would number
                # every untemplated column "Option 1".
                siblings = plan.comparison_id.plan_ids
                ahead = sum(
                    1 for other in siblings
                    if other != plan and other.sequence < plan.sequence
                )
                plan.name = _("Option %s", ahead + 1)
                plan.subtitle = False

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
