"""The client has accepted a plan: from quote to invoice in one step.

By hand this is five things in three apps -- confirm the right quotation,
cancel the plans they turned down, mark the opportunity won, create the
invoice, find it to send -- and the step most often skipped is the CRM one,
which is why pipelines fill with quotes that were won months ago.

A salesperson can do all of it without accounting rights: Odoo creates an
invoice from a sales order on their behalf (the same thing Create Invoice on
the order does), and leaves it in draft for accounts to post. Posting it here
is offered only to users who may post invoices anyway.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import RedirectWarning, UserError

_logger = logging.getLogger(__name__)


class MedicalQuoteAccept(models.TransientModel):
    _name = "odin.medical.quote.accept"
    _description = "Client accepted a medical quote"

    comparison_id = fields.Many2one(
        "odin.quote.comparison", required=True, readonly=True, ondelete="cascade")
    available_order_ids = fields.Many2many(
        "sale.order", compute="_compute_available_order_ids")
    order_id = fields.Many2one(
        "sale.order", string="Plan accepted", required=True,
        domain="[('id', 'in', available_order_ids)]",
    )
    currency_id = fields.Many2one(related="order_id.currency_id")
    amount_total = fields.Monetary(related="order_id.amount_total", string="Total premium")
    payment = fields.Selection(
        [
            ("full", "Pays in full"),
            ("instalments", "Pays in instalments"),
            ("later", "Confirm only, invoice later"),
        ],
        string="Payment", required=True, default="full",
    )
    available_instalment_plan_ids = fields.Many2many(
        "odin.quote.instalment.plan", compute="_compute_available_order_ids")
    instalment_plan_id = fields.Many2one(
        "odin.quote.instalment.plan", string="Instalment plan",
        domain="[('id', 'in', available_instalment_plan_ids)]",
    )
    instalment_amount = fields.Monetary(compute="_compute_schedule", string="Each instalment")
    instalment_payable = fields.Monetary(compute="_compute_schedule", string="Total payable")
    instalment_financing = fields.Monetary(compute="_compute_schedule", string="Financing")
    instalment_months = fields.Integer(compute="_compute_schedule")
    cover_start = fields.Date(
        string="Cover starts", required=True, default=fields.Date.context_today,
        help="The renewal is dated from it: a year on for annual cover.")
    post_invoice = fields.Boolean(
        string="Post the invoice now",
        help="Leave it unticked to have accounts check and post the draft.",
    )
    can_post = fields.Boolean(compute="_compute_can_post")
    other_order_ids = fields.Many2many("sale.order", compute="_compute_other_order_ids")
    handler_id = fields.Many2one(
        "res.users", string="Hand over to", domain=[("share", "=", False)],
        default=lambda self: self._default_handler_id(),
        help="Who places the cover with the insurer: they get a Place cover "
        "to-do with the checklist.",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if "cover_start" in fields_list:
            # A renewal runs on from the day the old cover ends.
            comparison = self.env["odin.quote.comparison"].browse(
                self.env.context.get("default_comparison_id") or []).exists()
            renewal = comparison.odin_renewal_id
            today = fields.Date.context_today(self)
            if renewal and renewal.renewal_date and renewal.renewal_date >= today:
                values["cover_start"] = renewal.renewal_date
        return values

    @api.model
    def _default_handler_id(self):
        comparison = self.env["odin.quote.comparison"].browse(
            self.env.context.get("default_comparison_id") or []).exists()
        if not comparison:
            return False
        return comparison.odin_handler_id or comparison._odin_default_handler()

    @api.depends("comparison_id", "order_id")
    def _compute_available_order_ids(self):
        for wizard in self:
            wizard.available_order_ids = wizard.comparison_id.plan_ids.order_id.filtered(
                lambda o: o.state in ("draft", "sent", "sale"))
            plans = wizard.comparison_id.instalment_plan_ids or \
                wizard.env["odin.quote.instalment.plan"].search(
                    [("company_id", "=", wizard.env.company.id)])
            if wizard.order_id:
                premium = wizard._odin_premium()
                plans = plans.filtered(lambda plan: plan._odin_available(premium))
            wizard.available_instalment_plan_ids = plans

    @api.depends("comparison_id", "order_id")
    def _compute_other_order_ids(self):
        for wizard in self:
            wizard.other_order_ids = (
                wizard.comparison_id.plan_ids.order_id - wizard.order_id
            ).filtered(lambda o: o.state in ("draft", "sent"))

    @api.depends("instalment_plan_id", "order_id.amount_total")
    def _compute_schedule(self):
        for wizard in self:
            schedule = wizard.instalment_plan_id.schedule(
                wizard._odin_premium()) if wizard.instalment_plan_id else {}
            wizard.instalment_amount = schedule.get("instalment", 0.0)
            wizard.instalment_payable = schedule.get("payable", 0.0)
            wizard.instalment_financing = schedule.get("financing", 0.0)
            wizard.instalment_months = schedule.get("months", 0)

    @api.onchange("payment")
    def _onchange_payment(self):
        if self.payment == "instalments" and not self.instalment_plan_id:
            self.instalment_plan_id = self.available_instalment_plan_ids[-1:]

    def _odin_premium(self):
        """What the instalments are worked out on: the order without any
        financing already on it."""
        order = self.order_id
        financing = order.order_line.filtered(lambda l: l.product_id.odin_instalment_interest)
        return order.amount_total - sum(financing.mapped("price_total"))

    def _odin_finance(self, order):
        """Paying by instalments, like a loan: the financing goes on the
        order and its invoice falls due in equal monthly parts, the first
        on the day it is issued."""
        plan = self.instalment_plan_id
        schedule = plan.schedule(self._odin_premium())
        product = self.env.ref("odin_medical_quote.product_financing")
        locked = order.locked
        if locked:
            order.action_unlock()
        old = order.order_line.filtered(
            lambda l: l.product_id == product
            or (l.display_type == "line_section" and l.benefit_key == "financing"))
        old.unlink()
        if order.currency_id.compare_amounts(schedule["financing"], 0) > 0:
            sequence = max(order.order_line.mapped("sequence") or [0])
            order.order_line = [
                fields.Command.create({
                    "display_type": "line_section",
                    "name": _("Paying by instalments"),
                    "benefit_label": _("Paying by instalments"),
                    "benefit_key": "financing",
                    "sequence": sequence + 1,
                }),
                fields.Command.create({
                    "product_id": product.id,
                    "name": plan.display_name,
                    "product_uom_qty": 1,
                    "price_unit": schedule["financing"],
                    "tax_ids": [fields.Command.clear()],
                    "sequence": sequence + 2,
                }),
            ]
        order.payment_term_id = plan._odin_payment_term()
        if locked:
            order.action_lock()

    def _compute_can_post(self):
        can_post = self.env.user.has_group("account.group_account_invoice")
        for wizard in self:
            wizard.can_post = can_post

    def action_accept(self):
        self.ensure_one()
        order = self.order_id
        comparison = self.comparison_id
        if order not in self.available_order_ids:
            raise UserError(_("Pick one of the plans on this quote."))
        if self.payment == "instalments" and not self.instalment_plan_id:
            raise UserError(_("Choose the instalment plan the client is paying by."))
        if self.payment == "instalments" and not self.instalment_plan_id._odin_available(
                self._odin_premium()):
            raise UserError(_("This premium is too small to pay by %s.",
                              self.instalment_plan_id.display_name))

        if self.payment == "instalments":
            self._odin_finance(order)
        if order.state in ("draft", "sent"):
            # Confirming closes out the quote (see sale.order._action_confirm):
            # the other plans cancelled, the opportunity won.
            result = order.with_context(odin_handler_id=self.handler_id.id,
                                        odin_cover_start=self.cover_start).action_confirm()
            if order.state != "sale":
                # Something on the order stopped it -- an exception rule or a
                # credit limit asking for approval. Its own dialog says what;
                # nothing else is done until the order goes through.
                if isinstance(result, dict):
                    return result
                raise UserError(_("%s could not be confirmed.", order.name))
        # Confirmed already, from the order itself: close the quote out now.
        comparison.with_context(odin_handler_id=self.handler_id.id,
                                odin_cover_start=self.cover_start)._odin_accept(order)
        if self.handler_id and comparison.odin_handler_id != self.handler_id:
            # Accepted before (on the portal, say): the to-do moves to who
            # was picked here.
            comparison.odin_handler_id = self.handler_id

        if self.payment == "later":
            return {
                "type": "ir.actions.act_window",
                "res_model": "sale.order",
                "res_id": order.id,
                "views": [(False, "form")],
            }
        if order.invoice_status != "to invoice":
            raise UserError(_("%s has nothing left to invoice.", order.name))
        invoices = order._create_invoices()
        # Printed and sent in the quotation's look from now on.
        report = self.env.ref("odin_medical_quote.action_report_insurance_invoice",
                              raise_if_not_found=False)
        partner = order.partner_id.commercial_partner_id.sudo()
        if report and not partner.invoice_template_pdf_report_id:
            partner.invoice_template_pdf_report_id = report
        if self.post_invoice and self.can_post:
            self._post(invoices)
        return order.action_view_invoice(invoices=invoices)

    def _post(self, invoices):
        """Post, but keep the sale if posting is refused: the draft is still
        there for accounts, and the reason is left on it."""
        for invoice in invoices:
            try:
                with self.env.cr.savepoint():
                    invoice.action_post()
            except (UserError, RedirectWarning) as error:
                _logger.info("Invoice %s left in draft: %s", invoice.id, error)
                invoice.message_post(body=_(
                    "Left in draft when the quote was accepted: %s", error.args[0]))
