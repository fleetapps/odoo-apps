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
from odoo.exceptions import UserError

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
    plan_summary = fields.Char(compute="_compute_plan_summary")
    currency_id = fields.Many2one(related="order_id.currency_id")
    amount_total = fields.Monetary(related="order_id.amount_total", string="Total premium")
    invoice_mode = fields.Selection(
        [
            ("full", "Invoice the full premium"),
            ("deposit", "Invoice a deposit"),
            ("later", "Confirm only, invoice later"),
        ],
        string="Invoice", required=True, default="full",
    )
    deposit_percent = fields.Float(
        string="Deposit (%)",
        default=lambda self: self._default_deposit_percent(),
    )
    deposit_amount = fields.Monetary(compute="_compute_deposit_amount")
    post_invoice = fields.Boolean(
        string="Post the invoice now",
        help="Leave it unticked to have accounts check and post the draft.",
    )
    can_post = fields.Boolean(compute="_compute_can_post")
    other_order_ids = fields.Many2many("sale.order", compute="_compute_other_order_ids")

    @api.model
    def _default_deposit_percent(self):
        plan = self.env["odin.quote.instalment.plan"].search(
            [("company_id", "=", self.env.company.id), ("deposit_percent", ">", 0)], limit=1)
        return plan.deposit_percent or 25.0

    @api.depends("comparison_id")
    def _compute_available_order_ids(self):
        for wizard in self:
            wizard.available_order_ids = wizard.comparison_id.plan_ids.order_id.filtered(
                lambda o: o.state in ("draft", "sent", "sale"))

    @api.depends("order_id")
    def _compute_plan_summary(self):
        for wizard in self:
            wizard.plan_summary = wizard.order_id.with_context(
                odin_plan_label=True).display_name or ""

    @api.depends("comparison_id", "order_id")
    def _compute_other_order_ids(self):
        for wizard in self:
            wizard.other_order_ids = (
                wizard.comparison_id.plan_ids.order_id - wizard.order_id
            ).filtered(lambda o: o.state in ("draft", "sent"))

    @api.depends("deposit_percent", "order_id.amount_total")
    def _compute_deposit_amount(self):
        for wizard in self:
            wizard.deposit_amount = wizard.currency_id.round(
                wizard.order_id.amount_total * wizard.deposit_percent / 100.0)

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
        if self.invoice_mode == "deposit" and not 0 < self.deposit_percent < 100:
            raise UserError(_("The deposit must be between 0% and 100% of the premium."))

        if order.state in ("draft", "sent"):
            result = order.action_confirm()
            if order.state != "sale":
                # Something on the order stopped it -- an exception rule or a
                # credit limit asking for approval. Its own dialog says what;
                # nothing else is done until the order goes through.
                if isinstance(result, dict):
                    return result
                raise UserError(_("%s could not be confirmed.", order.name))

        others = self.other_order_ids
        if others:
            others._action_cancel()
        lead = comparison.opportunity_id or order.opportunity_id
        if lead and lead.active and lead.won_status != "won":
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

        if self.invoice_mode == "later":
            return {
                "type": "ir.actions.act_window",
                "res_model": "sale.order",
                "res_id": order.id,
                "views": [(False, "form")],
            }
        invoices = self._create_invoices(order)
        if self.post_invoice and self.can_post:
            self._post(invoices)
        return order.action_view_invoice(invoices=invoices)

    def _create_invoices(self, order):
        if self.invoice_mode == "deposit":
            wizard = self.env["sale.advance.payment.inv"].with_context(
                active_model="sale.order", active_ids=order.ids, active_id=order.id,
            ).create({
                "advance_payment_method": "percentage",
                "amount": self.deposit_percent,
                "sale_order_ids": [fields.Command.set(order.ids)],
            })
            return wizard._create_invoices(order)
        if order.invoice_status != "to invoice":
            raise UserError(_("%s has nothing left to invoice.", order.name))
        return order._create_invoices()

    def _post(self, invoices):
        """Post, but keep the sale if posting is refused: the draft is still
        there for accounts, and the reason is left on it."""
        for invoice in invoices:
            try:
                with self.env.cr.savepoint():
                    invoice.action_post()
            except UserError as error:
                _logger.info("Invoice %s left in draft: %s", invoice.id, error)
                invoice.message_post(body=_(
                    "Left in draft when the quote was accepted: %s", error.args[0]))
