import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date, format_datetime, formatLang, html2plaintext, is_html_empty

#: sections that are money in or out, not a benefit with a row of its own
NOT_A_BENEFIT = frozenset({"statutory_charges", "payment_options", "discount", "policy_charge"})
#: characters of terms that fit in about half a page, kept on one page
SHORT_TERMS = 1800


class QuoteComparison(models.Model):
    """A cover comparison is the medical quote's record: the plans offered,
    who they were priced for, the opportunity they belong to, and -- once the
    client has chosen -- the plan they accepted."""

    _inherit = "odin.quote.comparison"

    state = fields.Selection(
        selection_add=[("accepted", "Accepted"), ("declined", "Declined")],
        ondelete={"accepted": "set default", "declined": "set default"},
    )
    opportunity_id = fields.Many2one(
        "crm.lead", string="Opportunity", index=True, tracking=True,
        ondelete="set null", copy=False,
    )
    odin_quote_type = fields.Selection(
        [("medical", "Medical")], string="Type", default="medical", required=True,
        index=True, copy=False,
        help="The insurance line quoted: it decides the screen Edit quote opens "
        "and what the quotation prints about the risk.")
    odin_builder_params = fields.Json(
        string="Quote builder input", copy=False,
        help="What was entered in Add Medical Quote, so Edit quote reopens it "
        "as it was left.",
    )
    odin_from_builder = fields.Boolean(compute="_compute_odin_from_builder")
    odin_accepted_order_id = fields.Many2one(
        "sale.order", string="Accepted quotation", readonly=True, copy=False,
    )
    odin_invoice_count = fields.Integer(compute="_compute_odin_invoice_count")

    # -- the register: what the list shows, searches and groups on ----------
    partner_phone = fields.Char(related="partner_id.phone", string="Phone")
    odin_customer_search = fields.Char(
        string="Customer, phone or email", compute="_compute_odin_customer_search",
        search="_search_odin_customer")
    odin_cover_summary = fields.Char(
        string="Who's covered", compute="_compute_odin_cover_summary", store=True)
    odin_plan_summary = fields.Char(
        string="Plans quoted", compute="_compute_odin_summary", store=True,
        help="The plans still on offer, or the one the client accepted.")
    odin_amount_total = fields.Monetary(
        string="Premium", compute="_compute_odin_summary", store=True,
        help="The accepted plan's total premium; before that the recommended "
        "plan's, or the lowest on the quote.")
    odin_handler_id = fields.Many2one(
        "res.users", string="Handled by", tracking=True, copy=False, index=True,
        domain=[("share", "=", False)],
        help="Who places the cover with the insurer once the client has "
        "accepted. They get the Place cover to-do; changing this moves it.")
    odin_order_count = fields.Integer(compute="_compute_odin_order_count")
    odin_placement_pending = fields.Boolean(
        string="Waiting to be placed", compute="_compute_odin_placement_pending",
        search="_search_odin_placement_pending")

    @api.depends("odin_builder_params")
    def _compute_odin_from_builder(self):
        for comparison in self:
            comparison.odin_from_builder = bool(comparison.odin_builder_params)

    @api.depends("odin_accepted_order_id.invoice_ids")
    def _compute_odin_invoice_count(self):
        for comparison in self:
            comparison.odin_invoice_count = len(comparison.odin_accepted_order_id.invoice_ids)

    @api.depends("plan_ids.order_id")
    def _compute_odin_order_count(self):
        for comparison in self:
            comparison.odin_order_count = len(comparison.plan_ids.order_id)

    def _compute_odin_customer_search(self):
        self.odin_customer_search = False

    @api.model
    def _search_odin_customer(self, operator, value):
        """Customer by name, email or phone -- a number typed 07... finds the
        +2547... stored on the contact."""
        if operator != "ilike" or not isinstance(value, str):
            return [("partner_id", operator, value)]
        domain = [("name", "ilike", value), ("email", "ilike", value), ("ref", "ilike", value)]
        domain += self.env["odin.medical.quote"]._odin_phone_domain(value)
        return [("partner_id", "any", ["|"] * (len(domain) - 1) + domain)]

    @api.depends("member_ids.role")
    def _compute_odin_cover_summary(self):
        for comparison in self:
            roles = comparison.member_ids.mapped("role")
            adults = sum(role in ("principal", "spouse") for role in roles)
            children = roles.count("child")
            parts = []
            if adults:
                parts.append(_("1 adult") if adults == 1 else _("%s adults", adults))
            if children:
                parts.append(_("1 child") if children == 1 else _("%s children", children))
            comparison.odin_cover_summary = ", ".join(parts)

    @api.depends("plan_ids.sequence", "plan_ids.highlight", "plan_ids.order_id.state",
                 "plan_ids.order_id.amount_total", "plan_ids.order_id.sale_order_template_id",
                 "odin_accepted_order_id")
    def _compute_odin_summary(self):
        for comparison in self:
            # A declined quote still says what was offered.
            plans = comparison._odin_export_plans() or comparison.plan_ids.sorted(
                lambda p: (p.sequence, p.id))
            comparison.odin_plan_summary = " · ".join(
                plan.order_id._odin_plan_label() or plan.name or plan.order_id.name
                for plan in plans)
            basis = plans.filtered("highlight")[:1] or plans
            amounts = basis.order_id.mapped("amount_total")
            comparison.odin_amount_total = min(amounts) if amounts else 0.0

    def _odin_place_cover_type(self):
        return self.env.ref("odin_medical_quote.mail_activity_type_place_cover",
                            raise_if_not_found=False)

    def _compute_odin_placement_pending(self):
        activity_type = self._odin_place_cover_type()
        for comparison in self:
            comparison.odin_placement_pending = bool(activity_type) and (
                activity_type in comparison.activity_ids.activity_type_id)

    @api.model
    def _search_odin_placement_pending(self, operator, value):
        if operator != "in":
            return NotImplemented
        activity_type = self._odin_place_cover_type()
        domain = [("activity_ids.activity_type_id", "=", activity_type.id if activity_type else 0)]
        if True in value and False in value:
            return []
        return domain if True in value else ["!", *domain]

    def write(self, vals):
        if "odin_handler_id" not in vals:
            return super().write(vals)
        handler = self.env["res.users"].browse(vals["odin_handler_id"] or [])
        moved = self.filtered(lambda c: c.odin_handler_id != handler)
        result = super().write(vals)
        if handler and moved:
            # The Place cover to-do follows the person handling it, who is
            # told (Odoo notifies a reassigned activity).
            activity_type = moved._odin_place_cover_type()
            moved.activity_ids.filtered(
                lambda a: a.activity_type_id == activity_type and a.user_id != handler
            ).write({"user_id": handler.id})
            moved.message_subscribe(partner_ids=handler.partner_id.ids)
        return result

    def _odin_default_handler(self):
        """Who places the cover when nobody was picked: the company's person
        for it, else the salesperson."""
        self.ensure_one()
        return (self.company_id.odin_quote_handler_id or self.user_id
                or self.opportunity_id.user_id or self.env.user)

    def _odin_hand_over(self, handler=None):
        """Give the accepted quote to the person placing the cover, with a
        Place cover to-do (its checklist is the activity type's note)."""
        activity_type = self._odin_place_cover_type()
        for comparison in self:
            person = handler or comparison.odin_handler_id or comparison._odin_default_handler()
            if comparison.odin_handler_id != person:
                comparison.odin_handler_id = person
            comparison.message_subscribe(partner_ids=person.partner_id.ids)
            if activity_type and activity_type not in comparison.activity_ids.activity_type_id:
                order = comparison.odin_accepted_order_id
                comparison.activity_schedule(
                    activity_type_id=activity_type.id,
                    summary=_("Place cover: %(plan)s for %(client)s",
                              plan=order._odin_plan_label() or order.name,
                              client=comparison.partner_id.name),
                    user_id=person.id,
                )

    def _odin_builder_tags(self):
        """The screen each type of quote is made and edited on."""
        return {"medical": ("odin_medical_quote", _("Add Medical Quote"))}

    def _odin_builder_action(self, **params):
        tag, name = self._odin_builder_tags()[self[:1].odin_quote_type or "medical"]
        return {
            "type": "ir.actions.client",
            "tag": tag,
            "name": name,
            "params": params,
        }

    def action_odin_edit(self):
        """Reopen the quote in Add Medical Quote, as it was saved."""
        self.ensure_one()
        if self.state == "accepted":
            raise UserError(_("%s has been accepted and can no longer change.", self.name))
        return self._odin_builder_action(comparison_id=self.id)

    def action_odin_accept(self):
        """The client has chosen: confirm their plan and invoice it."""
        self.ensure_one()
        if not self.plan_ids:
            raise UserError(_("There is no plan on this quote to accept."))
        orders = self.plan_ids.order_id.filtered(lambda o: o.state != "cancel")
        recommended = self.plan_ids.filtered("highlight").order_id[:1]
        return {
            "type": "ir.actions.act_window",
            "name": _("Client accepted"),
            "res_model": "odin.medical.quote.accept",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_comparison_id": self.id,
                "default_order_id": (recommended or orders[:1]).id,
                "odin_plan_label": True,
            },
        }

    def _odin_accept(self, order):
        """Record that the client chose ``order``.

        The other quotations are cancelled, the opportunity is won and the
        quote is accepted -- however the order came to be confirmed: Client
        accepted, Confirm on the order, or the client signing it on the
        portal. Without this a quote accepted on the portal left its siblings
        open and the opportunity in the pipeline for ever.
        """
        for comparison in self.filtered(lambda c: c.state != "accepted"):
            others = (comparison.plan_ids.order_id - order).filtered(
                lambda o: o.state in ("draft", "sent"))
            others._action_cancel()
            lead = comparison.opportunity_id or order.opportunity_id
            if lead and lead.won_status != "won":
                if not lead.active:
                    # Declined, then confirmed after all: a sale, not a loss.
                    lead.action_restore()
                lead.expected_revenue = order.amount_untaxed
                lead.action_set_won()
            comparison.write({"state": "accepted", "odin_accepted_order_id": order.id})
            comparison.message_post(body=_(
                "Client accepted %(plan)s (%(order)s). %(others)s",
                plan=order._odin_plan_label() or order.name,
                order=order.name,
                others=_("Other plans cancelled: %s.", ", ".join(others.mapped("name")))
                if others else "",
            ))
            # Picked in Client accepted, passed down through the confirmation.
            handler = self.env["res.users"].browse(
                self.env.context.get("odin_handler_id") or []).exists()
            comparison._odin_hand_over(handler or None)

    def action_odin_decline(self):
        """The client said no: mark the opportunity lost, with CRM's own lost
        reason dialog, and the quote declined (see crm.lead.action_set_lost)."""
        self.ensure_one()
        lead = self.opportunity_id
        if not lead or not lead.active:
            self._odin_decline()
            return True
        action = self.env["ir.actions.actions"]._for_xml_id("crm.crm_lead_lost_action")
        action["context"] = {
            "default_lead_ids": lead.ids,
            "active_ids": lead.ids,
            "active_model": "crm.lead",
            "dialog_size": "medium",
        }
        return action

    def _odin_decline(self):
        """The quotations still open are cancelled and the quote declined, so
        nothing is left in Quotations for a client who said no."""
        for comparison in self.filtered(lambda c: c.state in ("draft", "sent")):
            orders = comparison.plan_ids.order_id.filtered(lambda o: o.state in ("draft", "sent"))
            orders._action_cancel()
            comparison.write({"state": "declined"})
            comparison.message_post(body=_(
                "The client declined. Quotations cancelled: %s.",
                ", ".join(orders.mapped("name")) or _("none")))

    def action_odin_open_opportunity(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.lead",
            "res_id": self.opportunity_id.id,
            "views": [(False, "form")],
        }

    def action_odin_view_invoices(self):
        self.ensure_one()
        return self.odin_accepted_order_id.action_view_invoice()

    @api.model
    def action_odin_new_quote(self):
        return self._odin_builder_action()

    # -- getting it to the client -----------------------------------------

    def action_print(self):
        """A quote made in Add Medical Quote prints as the medical quotation."""
        if len(self) == 1 and self.odin_from_builder:
            return self.env.ref(
                "odin_medical_quote.action_report_medical_quotation").report_action(self)
        return super().action_print()

    def _odin_export_filename(self):
        self.ensure_one()
        return _("%(line)s quotation - %(client)s - %(ref)s",
                 line=dict(self._fields["odin_quote_type"]._description_selection(self.env)).get(
                     self.odin_quote_type, ""),
                 client=self.partner_id.name or "", ref=self.name)

    def _comparison_pdf_attachment(self):
        """What Share on WhatsApp sends: the medical quotation, for a quote
        made in Add Medical Quote."""
        self.ensure_one()
        if not self.odin_from_builder:
            return super()._comparison_pdf_attachment()
        pdf, _ext = self.env["ir.actions.report"]._render_qweb_pdf(
            "odin_medical_quote.report_medical_quotation", self.ids)
        return self.env["ir.attachment"].create({
            "name": "%s.pdf" % self._odin_export_filename(),
            "type": "binary",
            "datas": base64.b64encode(pdf),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/pdf",
        })

    def _odin_email_template(self):
        """The email a quote of this type goes out with."""
        return self.env.ref("odin_medical_quote.mail_template_medical_quote",
                            raise_if_not_found=False)

    def action_odin_send_email(self):
        """Email the quotation to the client, the PDF attached."""
        self.ensure_one()
        if not self.plan_ids:
            raise UserError(_("There is nothing to send until the quote has plans."))
        template = self._odin_email_template()
        return {
            "type": "ir.actions.act_window",
            "name": _("Send by email"),
            "res_model": "mail.compose.message",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_model": self._name,
                "default_res_ids": self.ids,
                "default_template_id": template.id if template else False,
                "default_composition_mode": "comment",
                "default_email_layout_xmlid":
                    "mail.mail_notification_layout_with_responsible_signature",
                "force_email": True,
                "mark_odin_quote_sent": True,
            },
        }

    def message_post(self, **kwargs):
        if self.env.context.get("mark_odin_quote_sent"):
            self.filtered(lambda c: c.state == "draft").write({"state": "sent"})
        return super().message_post(**kwargs)

    # -- the medical quotation (report) -----------------------------------

    def _odin_export_plans(self):
        """The plans a quotation shows: what is still on offer, or only the
        plan the client took once they have accepted."""
        self.ensure_one()
        plans = self.plan_ids.sorted(lambda p: (p.sequence, p.id))
        if self.odin_accepted_order_id:
            return plans.filtered(lambda p: p.order_id == self.odin_accepted_order_id)
        return plans.filtered(lambda p: p.order_id.state != "cancel")

    def _odin_export_members(self):
        """Who the quotation is for, as label/value pairs: dates of birth as
        entered, the children, the limit range asked for."""
        self.ensure_one()
        cover = (self.odin_builder_params or {}).get("cover") or {}
        if not cover:
            return [(d.label, d.value) for d in self.detail_ids if (d.label or "").strip()]

        def date(value):
            return self._odin_export_date(fields.Date.to_date(value)) if value else ""

        def amount(value):
            try:
                number = float(str(value).replace(",", ""))
            except ValueError:
                return ""
            return formatLang(self.env, number, digits=0) if number else ""

        pairs = []
        if not cover.get("children_only"):
            if cover.get("principal_age") not in (None, "", False):
                # Ages, as the screen and the online form take them.
                pairs.append((_("Principal age"), str(cover.get("principal_age") or "")))
                if cover.get("include_spouse"):
                    pairs.append((_("Spouse age"), str(cover.get("spouse_age") or "")))
            else:
                pairs.append((_("Principal DOB"), date(cover.get("principal_dob"))))
                if cover.get("include_spouse"):
                    pairs.append((_("Spouse DOB"), date(cover.get("spouse_dob"))))
        pairs.append((_("Children"), str(int(cover.get("children") or 0))))
        if cover.get("template_id"):
            template = self.env["sale.order.template"].browse(int(cover["template_id"])).exists()
            pairs.append((_("Plan requested"), template.name or ""))
        else:
            low, high = amount(cover.get("limit_min")), amount(cover.get("limit_max"))
            if low or high:
                pairs.append((_("Requested limit"), "%s — %s" % (low or "0", high or _("any"))))
        if cover.get("insurer"):
            pairs.append((_("Insurer"), cover["insurer"]))
        return pairs

    def _odin_export_member_headings(self):
        """The heading and table title of the who-or-what-is-covered block."""
        return _("Member details"), _("People included in this quotation")

    def _odin_export_member_rows(self, columns=2):
        """The member details in rows of ``columns`` label/value pairs."""
        pairs = self._odin_export_members()
        pairs += [None] * ((columns - len(pairs) % columns) % columns)
        return [pairs[i:i + columns] for i in range(0, len(pairs), columns)]

    def _odin_export_matrix(self):
        """The comparison table of the medical quotation.

        One column per plan, headed by its insurer and plan. Rows, in the
        order the client reads them: what each benefit pays out, what it
        costs, any discount, taxes and levies (the levies and stamp duty
        together, as the client pays them), any policy charge, the total, and
        what paying by instalments comes to. Every figure is read from the
        quotations, so the PDF can never disagree with them.
        """
        self.ensure_one()
        plans = self._odin_export_plans()
        columns = []
        for plan in plans:
            order = plan.order_id
            sections = {s["key"]: s for s in order._client_sections() if s["included"]}
            columns.append({
                "plan": plan,
                "insurer": order._odin_insurer_label() or plan.name or "",
                "name": order._odin_plan_name() or plan.subtitle or order.name,
                "highlight": plan.highlight,
                "sections": sections,
                "amounts": order._client_amounts(),
            })

        keys, labels = [], {}
        for column in columns:
            for key, section in column["sections"].items():
                if key not in NOT_A_BENEFIT and key not in labels:
                    keys.append(key)
                    labels[key] = section["label"]

        def cells(getter):
            return [getter(column) for column in columns]

        def section_value(key, field):
            """None where the plan has no such section; an amount of 0 where
            it has, which prints as Included (a limit of 0 is no limit)."""
            def get(column):
                section = column["sections"].get(key)
                if not section:
                    return None
                value = section.get(field) or 0.0
                return value if field == "amount" else (value or None)
            return get

        rows = []
        for key in keys:
            limits = cells(section_value(key, "limit"))
            if any(limits):
                rows.append({"kind": "limit", "label": _("%s limit", labels[key]), "cells": limits})
        for key in keys:
            rows.append({"kind": "premium", "label": labels[key],
                         "cells": cells(section_value(key, "amount"))})
        discounts = cells(section_value("discount", "amount"))
        if any(discounts):
            rows.append({"kind": "discount", "label": _("Discount"), "cells": discounts})
        rows.append({
            "kind": "levies",
            "label": _("Taxes and levies"),
            "cells": cells(lambda column: column["amounts"]["tax"] + (
                (column["sections"].get("statutory_charges") or {}).get("amount") or 0.0)),
        })
        policy = cells(section_value("policy_charge", "amount"))
        if any(policy):
            rows.append({"kind": "charge", "label": _("Policy charge"), "cells": policy})
        rows.append({"kind": "total", "label": _("Total premium"),
                     "cells": cells(lambda column: column["amounts"]["total"])})
        for instalment in self.instalment_plan_ids:
            schedules = cells(lambda column, plan=instalment: plan.schedule(
                column["amounts"]["total"]))
            label = _("%s monthly instalments", instalment.months)
            rows.append({"kind": "instalment", "label": label, "cells": schedules})
        return {"columns": columns, "rows": rows}

    def _odin_export_terms(self):
        """The terms of the plans quoted, each once.

        Plans of one product share their terms; printing them per plan is
        how a three-plan quotation ends up with the same two pages three
        times over. Headed by the product when two products' terms differ.
        """
        self.ensure_one()
        terms, seen = [], set()
        for plan in self._odin_export_plans():
            note = plan.order_id.note
            if is_html_empty(note) or str(note) in seen:
                continue
            seen.add(str(note))
            template = plan.order_id.sale_order_template_id
            terms.append({
                "title": template._odin_heading()[0] if template else "",
                "note": note,
                # Under about half a page: kept whole with its heading.
                "short": len(html2plaintext(note)) < SHORT_TERMS,
            })
        if len(terms) == 1:
            terms[0]["title"] = ""
        if not terms and not is_html_empty(self.note):
            terms.append({"title": "", "note": self.note,
                          "short": len(html2plaintext(self.note)) < SHORT_TERMS})
        return terms

    def _odin_export_payment(self):
        """How to pay, as printed under the quotation."""
        self.ensure_one()
        return self.company_id._odin_payment_html()

    def _odin_export_date(self, value):
        """``21 Jan 1984``: every date on the quotation, day first and with the
        month in words, so 01/21 and 21/01 cannot be read the wrong way round
        whatever the language's date format."""
        return format_date(self.env, value, date_format="d MMM y") if value else ""

    def _odin_export_printed(self):
        """'Caroline Chege on 21 Oct 2026 10:20' for the footer, in the
        user's time zone."""
        user = self.env.user
        if user.share:
            # Printed for the client from their own link: the salesperson's
            # quote, not "Public user"'s.
            user = self.user_id or self.company_id.partner_id
        return _("%(user)s on %(when)s",
                 user=user.name,
                 when=format_datetime(self.env, fields.Datetime.now(), dt_format="d MMM y HH:mm"))

    def _odin_export_benefit_width(self, count):
        """Width of the benefit column, in %, for ``count`` plan columns: one
        plan gets as much room as the benefits, four still fit across A4."""
        return {1: 55, 2: 40, 3: 34}.get(count, 28)


class QuoteComparisonPlan(models.Model):
    _inherit = "odin.quote.comparison.plan"

    odin_order_state = fields.Selection(related="order_id.state", string="Status")
    odin_template_id = fields.Many2one(
        related="order_id.sale_order_template_id", string="Plan")
