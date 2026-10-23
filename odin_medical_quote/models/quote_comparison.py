import base64

from markupsafe import Markup

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

    @api.depends("odin_builder_params")
    def _compute_odin_from_builder(self):
        for comparison in self:
            comparison.odin_from_builder = bool(comparison.odin_builder_params)

    @api.depends("odin_accepted_order_id.invoice_ids")
    def _compute_odin_invoice_count(self):
        for comparison in self:
            comparison.odin_invoice_count = len(comparison.odin_accepted_order_id.invoice_ids)

    def _odin_builder_action(self, **params):
        return {
            "type": "ir.actions.client",
            "tag": "odin_medical_quote",
            "name": _("Add Medical Quote"),
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
                plan=order.sale_order_template_id.name or order.name,
                order=order.name,
                others=_("Other plans cancelled: %s.", ", ".join(others.mapped("name")))
                if others else "",
            ))

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
        return _("Medical quotation - %(client)s - %(ref)s",
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

    def action_odin_send_email(self):
        """Email the quotation to the client, the PDF attached."""
        self.ensure_one()
        if not self.plan_ids:
            raise UserError(_("There is nothing to send until the quote has plans."))
        template = self.env.ref(
            "odin_medical_quote.mail_template_medical_quote", raise_if_not_found=False)
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
            template = order.sale_order_template_id
            sections = {s["key"]: s for s in order._client_sections() if s["included"]}
            columns.append({
                "plan": plan,
                "insurer": template._odin_insurer_name() if template else (plan.name or ""),
                "name": (template._odin_heading()[1] or template.name) if template
                else (plan.subtitle or order.name),
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
            return lambda column: (column["sections"].get(key) or {}).get(field) or None

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
            label = (_("%(months)s months, %(deposit)s%% deposit",
                       months=instalment.months, deposit=round(instalment.deposit_percent))
                     if instalment.deposit_percent
                     else _("%s months", instalment.months))
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
        """How to pay, as printed under the quotation: the company's own
        wording when it has set it, otherwise its bank accounts."""
        self.ensure_one()
        company = self.company_id
        if not is_html_empty(company.odin_quote_payment_note):
            return company.odin_quote_payment_note
        accounts = company.partner_id.bank_ids
        if not accounts:
            return False
        lines = []
        for account in accounts:
            lines.append(Markup("<b>%s</b>") % (account.bank_id.name or _("Bank account")))
            lines.append(_("A/C name: %s", account.acc_holder_name or company.name))
            lines.append(_("A/C no: %s", account.acc_number))
            if account.bank_id.bic:
                lines.append(_("SWIFT: %s", account.bank_id.bic))
        return Markup("<br/>").join(lines)

    def _odin_export_date(self, value):
        """``21 Jan 1984``: every date on the quotation, day first and with the
        month in words, so 01/21 and 21/01 cannot be read the wrong way round
        whatever the language's date format."""
        return format_date(self.env, value, date_format="d MMM y") if value else ""

    def _odin_export_printed(self):
        """'Caroline Chege on 21 Oct 2026 10:20' for the footer, in the
        user's time zone."""
        return _("%(user)s on %(when)s",
                 user=self.env.user.name,
                 when=format_datetime(self.env, fields.Datetime.now(), dt_format="d MMM y HH:mm"))

    def _odin_export_benefit_width(self, count):
        """Width of the benefit column, in %, for ``count`` plan columns: one
        plan gets as much room as the benefits, four still fit across A4."""
        return {1: 55, 2: 40, 3: 34}.get(count, 28)
