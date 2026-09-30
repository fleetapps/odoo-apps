# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Shopify Payments payout import + reconciliation.

A payout groups the balance transactions (charges, refunds, fees, disputes,
adjustments) that Shopify deposited to the merchant's bank on a given day.
This module:

1. imports payouts and their transactions (REST Shopify Payments API - only
   available when the store uses Shopify Payments; a 404 degrades gracefully);
2. auto-matches each order-linked transaction to its Odoo sale order and
   posted invoice by Shopify order id;
3. optionally registers payment on the matched invoices against a Shopify
   Payments clearing journal, so the payout's invoices show paid and net to
   the deposited amount.

Money is only moved by the explicit "Register Payments" action (or the opt-in
per-instance auto flag) - never silently on import.
"""
import json
import logging
import re

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

#: Balance-transaction types that correspond to a customer order.
ORDER_TXN_TYPES = ("charge", "refund", "dispute")


def _next_page_info(link_header):
    """Extract the ``page_info`` cursor of the rel="next" link, if any."""
    for part in (link_header or "").split(","):
        if 'rel="next"' in part:
            match = re.search(r"[?&]page_info=([^&>]+)", part)
            return match.group(1) if match else None
    return None


class ShopifyPayout(models.Model):
    _name = "shopify.bisync.payout"
    _description = "Shopify Payout"
    _order = "date desc, id desc"
    _rec_name = "shopify_payout_id"

    _uniq_payout = models.Constraint(
        "UNIQUE(instance_id, shopify_payout_id)",
        "This payout is already imported.")

    instance_id = fields.Many2one(
        "shopify.bisync.instance", required=True, ondelete="cascade",
        index=True)
    company_id = fields.Many2one(
        related="instance_id.company_id", store=True, index=True)
    shopify_payout_id = fields.Char(required=True, index=True)
    date = fields.Date(index=True)
    status = fields.Char()
    currency_id = fields.Many2one("res.currency")
    amount = fields.Monetary(currency_field="currency_id",
                             help="Net amount deposited by Shopify.")
    charge_total = fields.Monetary(currency_field="currency_id")
    refund_total = fields.Monetary(currency_field="currency_id")
    fee_total = fields.Monetary(currency_field="currency_id")
    adjustment_total = fields.Monetary(currency_field="currency_id")
    transaction_ids = fields.One2many(
        "shopify.bisync.payout.transaction", "payout_id")
    # Stored: the payout search view filters on unmatched_count, and Odoo
    # rejects a domain on a non-stored compute at install time
    # ("Unsearchable field"). The depends below are all stored columns, so
    # the ORM keeps these in sync on its own.
    transaction_count = fields.Integer(compute="_compute_stats", store=True)
    matched_count = fields.Integer(compute="_compute_stats", store=True)
    unmatched_count = fields.Integer(compute="_compute_stats", store=True)
    net_total = fields.Monetary(
        compute="_compute_stats", store=True, currency_field="currency_id",
        string="Transactions Net",
        help="Sum of the net amounts of this payout's transactions.")
    difference = fields.Monetary(
        compute="_compute_stats", store=True, currency_field="currency_id",
        help="Deposited amount minus the sum of the transactions. Anything "
             "other than zero means a transaction is missing or mis-typed - "
             "the payout does not close, and the bank line will not either.")
    fee_move_id = fields.Many2one(
        "account.move", readonly=True, copy=False, string="Fee Entry",
        help="Journal entry booking this payout's processing fees.")
    state = fields.Selection(
        [("imported", "Imported"), ("reconciled", "Reconciled")],
        default="imported", index=True)

    @api.depends("transaction_ids", "transaction_ids.matched",
                 "transaction_ids.transaction_type", "transaction_ids.net",
                 "amount")
    def _compute_stats(self):
        for payout in self:
            order_txns = payout.transaction_ids.filtered(
                lambda t: t.transaction_type in ORDER_TXN_TYPES)
            payout.transaction_count = len(payout.transaction_ids)
            payout.matched_count = len(order_txns.filtered("matched"))
            payout.unmatched_count = len(order_txns.filtered(
                lambda t: not t.matched))
            payout.net_total = sum(payout.transaction_ids.mapped("net"))
            payout.difference = payout.amount - payout.net_total

    # ------------------------------------------------------- reconciliation -
    def action_register_payments(self):
        """Close the payout: register payment on every matched, still-open
        invoice, then book the fees.

        Payments alone do not close anything. They move the GROSS amount into
        the clearing account while Shopify only deposits the NET, so without
        the fee entry the difference sits in the clearing account forever and
        the bank statement line never reconciles - which is where every other
        connector's "payout import" stops being useful.

        Idempotent: paid invoices are skipped and the fee entry is posted once.
        """
        for payout in self:
            journal = (payout.instance_id.payout_journal_id
                       or payout.instance_id.payment_journal_id)
            if not journal:
                raise UserError(_(
                    "Set a Payout Journal (or Payment Journal) on store %s "
                    "before registering payout payments.",
                    payout.instance_id.name))
            payout.transaction_ids._register_payment(journal)
            payout._book_fees(strict=False)
            payout.state = "reconciled"
        return True

    def action_book_fees(self):
        """Button: post the fee entry, complaining loudly if unconfigured."""
        for payout in self:
            payout._book_fees(strict=True)
        return True

    def _book_fees(self, strict=True):
        """Post one journal entry moving fees (and adjustments) out of the
        clearing account and into the fee expense account."""
        self.ensure_one()
        instance = self.instance_id
        journal = instance.payout_fee_journal_id
        expense = instance.payout_fee_account_id
        clearing = (instance.payout_journal_id
                    or instance.payment_journal_id).default_account_id
        fees, adjustments = self.fee_total, self.adjustment_total
        if self.fee_move_id or not (fees or adjustments):
            return False
        missing = not (journal and expense and clearing)
        # Payout currency != company currency needs an FX policy this module
        # does not get to invent, so it is refused rather than guessed.
        foreign = (self.currency_id
                   and self.currency_id != self.company_id.currency_id)
        if missing or foreign:
            reason = (_("the payout is in %(cur)s but the company books in "
                        "%(company)s", cur=self.currency_id.name,
                        company=self.company_id.currency_id.name)
                      if foreign else
                      _("the store has no Fee Journal / Fee Expense Account / "
                        "payout journal account configured"))
            if strict:
                raise UserError(_(
                    "Cannot book the fees of payout %(payout)s: %(reason)s.",
                    payout=self.shopify_payout_id, reason=reason))
            _logger.info("shopify_bisync: fees not booked for payout %s (%s)",
                         self.shopify_payout_id, reason)
            return False
        lines = []
        if fees:
            lines.append(fields.Command.create({
                "account_id": expense.id,
                "name": _("Shopify Payments fees %s", self.shopify_payout_id),
                "balance": fees}))
        if adjustments:
            lines.append(fields.Command.create({
                "account_id": expense.id,
                "name": _("Shopify payout adjustments %s",
                          self.shopify_payout_id),
                "balance": -adjustments}))
        lines.append(fields.Command.create({
            "account_id": clearing.id,
            "name": _("Shopify payout %s", self.shopify_payout_id),
            "balance": adjustments - fees}))
        move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": journal.id,
            "date": self.date or fields.Date.today(),
            "ref": _("Shopify payout %s", self.shopify_payout_id),
            "company_id": self.company_id.id,
            "line_ids": lines,
        })
        move.action_post()
        self.fee_move_id = move
        return True

    def action_open_fee_move(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move",
                "res_id": self.fee_move_id.id, "view_mode": "form"}


class ShopifyPayoutTransaction(models.Model):
    _name = "shopify.bisync.payout.transaction"
    _description = "Shopify Payout Transaction"
    _order = "id desc"

    _uniq_txn = models.Constraint(
        "UNIQUE(payout_id, shopify_transaction_id)",
        "This transaction is already imported.")

    payout_id = fields.Many2one(
        "shopify.bisync.payout", required=True, ondelete="cascade", index=True)
    instance_id = fields.Many2one(
        related="payout_id.instance_id", store=True, index=True)
    company_id = fields.Many2one(
        related="payout_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="payout_id.currency_id", store=True)
    shopify_transaction_id = fields.Char(required=True, index=True)
    transaction_type = fields.Char(index=True)
    amount = fields.Monetary(currency_field="currency_id")
    fee = fields.Monetary(currency_field="currency_id")
    net = fields.Monetary(currency_field="currency_id")
    source_order_id = fields.Char(index=True, help="Shopify order id.")
    sale_order_id = fields.Many2one("sale.order", ondelete="set null")
    invoice_id = fields.Many2one("account.move", ondelete="set null")
    matched = fields.Boolean(index=True)

    def _register_payment(self, journal):
        Register = self.env["account.payment.register"]
        for txn in self.filtered(
                lambda t: t.transaction_type == "charge" and t.invoice_id
                and t.invoice_id.payment_state not in ("paid", "in_payment")):
            try:
                Register.with_context(
                    active_model="account.move",
                    active_ids=txn.invoice_id.ids,
                    # never bounce this payment back to Shopify as mark-as-paid
                    shopify_bisync_skip_paid_push=True,
                ).create({"journal_id": journal.id}).action_create_payments()
            except Exception:  # noqa: BLE001 - one bad invoice must not stop the run
                _logger.exception("shopify_bisync: payout payment failed for %s",
                                  txn.invoice_id.name)


class PayoutSync(models.AbstractModel):
    _name = "shopify.bisync.payout.sync"
    _description = "Payout Import Engine"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        self._import_payout(job.instance_id, payload)

    @api.model
    def _import_payout(self, instance, payout_dict):
        Payout = self.env["shopify.bisync.payout"]
        Txn = self.env["shopify.bisync.payout.transaction"]
        currency = self.env["res.currency"].search(
            [("name", "=", (payout_dict.get("currency") or "").upper())],
            limit=1)
        payout = Payout.search([
            ("instance_id", "=", instance.id),
            ("shopify_payout_id", "=", str(payout_dict["id"]))], limit=1)
        summary = payout_dict.get("summary") or {}
        vals = {
            "instance_id": instance.id,
            "shopify_payout_id": str(payout_dict["id"]),
            "date": payout_dict.get("date"),
            "status": payout_dict.get("status"),
            "currency_id": currency.id or False,
            "amount": float(payout_dict.get("amount") or 0),
            "charge_total": float(summary.get("charges_gross_amount") or 0),
            "refund_total": float(summary.get("refunds_fee_amount") or 0)
            + float(summary.get("refunds_gross_amount") or 0),
            "fee_total": float(summary.get("charges_fee_amount") or 0),
            "adjustment_total": float(
                summary.get("adjustments_gross_amount") or 0),
        }
        payout = payout.write(vals) and payout or Payout.create(vals)
        # Pull this payout's balance transactions (cursor-paginated REST).
        params = {"payout_id": payout_dict["id"], "limit": 250}
        endpoint = "shopify_payments/balance/transactions.json"
        while True:
            try:
                body, headers = instance.api_call_raw(
                    "GET", endpoint, params=params)
            except UserError as exc:
                _logger.warning("shopify_bisync: payout txns fetch failed: %s",
                                exc)
                break
            for row in body.get("transactions", []):
                if Txn.search_count([
                        ("payout_id", "=", payout.id),
                        ("shopify_transaction_id", "=", str(row["id"]))]):
                    continue
                txn = Txn.create({
                    "payout_id": payout.id,
                    "shopify_transaction_id": str(row["id"]),
                    "transaction_type": row.get("type"),
                    "amount": float(row.get("amount") or 0),
                    "fee": float(row.get("fee") or 0),
                    "net": float(row.get("net") or 0),
                    "source_order_id": str(row.get("source_order_id") or "")
                    or False,
                })
                self._match_transaction(instance, txn)
            cursor = _next_page_info(headers.get("Link"))
            if not cursor:
                break
            params = {"page_info": cursor, "limit": 250}
        if (instance.payout_auto_reconcile
                and payout.state != "reconciled"):
            payout.action_register_payments()
        instance.last_import_payouts = fields.Datetime.now()

    @api.model
    def _match_transaction(self, instance, txn):
        """Link an order transaction to its Odoo sale order + posted invoice."""
        if (txn.transaction_type not in ORDER_TXN_TYPES
                or not txn.source_order_id):
            return
        binding = self.env["shopify.bisync.binding"].get(
            self.env, instance, "sale.order", external_id=txn.source_order_id)
        so = binding and binding.resolve()
        if not so:
            so = self.env["sale.order"].search([
                ("shopify_bisync_instance_id", "=", instance.id),
                ("shopify_bisync_order_id", "=", txn.source_order_id)], limit=1)
        if not so:
            return
        invoice = so.invoice_ids.filtered(
            lambda m: m.move_type == "out_invoice"
            and m.state == "posted")[:1]
        txn.write({"sale_order_id": so.id,
                   "invoice_id": invoice.id or False,
                   "matched": bool(invoice)})


class PayoutCron(models.AbstractModel):
    """Thin cron holder so ir.cron can target a stable model."""
    _name = "shopify.bisync.payout.cron"
    _description = "Shopify Payout Cron"

    @api.model
    def cron_import_payouts(self):
        Job = self.env["shopify.bisync.job"]
        Instance = self.env["shopify.bisync.instance"]
        for instance in Instance.search([("import_payouts", "=", True)]):
            since = (instance.last_import_payouts
                     or fields.Datetime.now() - timedelta(days=7)).date()
            try:
                payouts = instance.api_call(
                    "GET", "shopify_payments/payouts.json",
                    params={"date_min": since.isoformat(), "limit": 250}
                ).get("payouts", [])
            except UserError as exc:
                # Store without Shopify Payments -> endpoint 404s. Skip quietly.
                _logger.info("shopify_bisync: no payouts for %s (%s)",
                             instance.name, exc)
                continue
            for payout in payouts:
                Job.enqueue(instance, "in", "payout", payout, priority=40,
                            lock_key=f"payout:{payout['id']}")
