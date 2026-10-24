# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Receipts into the bank, and the bank into the books.

Every confirmed receipt becomes one statement line in its shortcode's
journal, ``unique_import_id = "MPESA-<receipt>"``, grouped into one statement
per till per day. That line is then reconciled through account_reconcile_oca,
the same machinery an accountant drives from the reconciliation widget, so
what the engine did can be read and undone there.

Matching rules, in order, each switchable per company:

1. STK receipts already know their document (invoice, portal payment;
   POS orders settle at session close).
2. POS "Paid to till" claims (x_ke_mpesa_pos): settled at session close.
3. Paybill account number = invoice number -> that invoice; = customer or
   unit reference -> that customer's oldest open invoices, the rest on
   account.
4. Anything else stays unallocated, in the queue.
"""

import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


# A customer receipt's statement line. Charges, sweeps and transfers carry a
# suffix (MPESA-<receipt>-CHG...) and are not receipts.
RECEIPT_UID_RE = re.compile(r"MPESA-([A-Z0-9]{8,12})")


def normalise_ref(value):
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


class MpesaTransaction(models.Model):
    _inherit = "mpesa.transaction"

    # ------------------------------------------------------------------
    # Statement lines
    # ------------------------------------------------------------------

    def _unique_import_id(self):
        self.ensure_one()
        return f"MPESA-{self.receipt}"

    def _statement_line_vals(self, journal):
        self.ensure_one()
        channel = dict(self._fields["channel"].selection).get(self.channel, "")
        label = " ".join(filter(None, [self.receipt, channel, self.customer_name,
                                       self.bill_ref and f"[{self.bill_ref}]"]))
        return {
            "journal_id": journal.id,
            "date": self.trans_date or fields.Date.context_today(self),
            "amount": self.amount,
            "payment_ref": label,
            "ref": self.receipt,
            "narration": f"{channel} · {self.shortcode_id.display_name}",
            "partner_id": self.partner_id.id or False,
            "partner_name": self.customer_name or False,
            "transaction_type": self.transaction_type or channel,
            "unique_import_id": self._unique_import_id(),
            "mpesa_transaction_id": self.id,
        }

    def _ensure_statement_line(self):
        """Exactly one statement line per confirmed receipt."""
        Line = self.env["account.bank.statement.line"].sudo()
        for tx in self:
            if tx.state != "confirmed" or not tx.receipt or tx.statement_line_id:
                continue
            journal = tx.shortcode_id.journal_id
            if not journal:
                if tx.exception == "none":
                    tx.write({"exception": "no_journal", "exception_note": _(
                        "%s has no journal: set up its accounting, then "
                        "use Retry on the receipt.", tx.shortcode_id.display_name)})
                continue
            existing = Line.search([("unique_import_id", "=", tx._unique_import_id())],
                                   limit=1)
            if existing:
                # A statement file or the Pull API got here first.
                existing.mpesa_transaction_id = tx
                tx.statement_line_id = existing
                continue
            vals = tx._statement_line_vals(journal)
            vals["statement_id"] = journal._mpesa_daily_statement(vals["date"]).id
            line = Line.with_context(skip_mpesa_hook=True).create(vals)
            tx.statement_line_id = line
            if tx.exception == "no_journal":
                tx.write({"exception": "none", "exception_note": False})
            line.statement_id._mpesa_sync_balance()

    def action_retry(self):
        """Run the statement line and matching again (after a fix)."""
        self.filtered(lambda t: t.exception == "no_journal").write(
            {"exception": "none", "exception_note": False})
        self._after_confirm(notify=False)
        return True

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _auto_match(self):
        for tx in self:
            if tx.allocation_state != "unallocated":
                continue
            company = tx.company_id
            if company.mpesa_match_stk and tx._match_origin():
                continue
            if company.mpesa_match_claims and tx._match_claim():
                continue
            if company.mpesa_match_paybill and tx.bill_ref and tx._match_bill_ref():
                continue

    def _match_origin(self):
        """Rule 1. Overridden for POS orders and portal payments."""
        self.ensure_one()
        if self.move_id and self.move_id.state == "posted":
            return self._allocate_to_invoices(self.move_id, rest="partner")
        return False

    def _match_claim(self):
        """Rule 2, provided by x_ke_mpesa_pos."""
        return False

    def _match_bill_ref(self):
        """Rule 3: the paybill account number names an invoice or a customer."""
        self.ensure_one()
        ref = normalise_ref(self.bill_ref)
        if not ref:
            return False
        invoices = self._invoices_by_reference(ref)
        if len(invoices) == 1:
            return self._allocate_to_invoices(invoices, rest="partner")
        partner = self._partner_by_reference(ref)
        if partner:
            invoices = self._open_invoices(partner)
            if invoices:
                return self._allocate_to_invoices(invoices, rest="partner", partner=partner)
            return self._allocate_on_account(partner)
        return False

    def _invoices_by_reference(self, ref):
        self.env.cr.execute("""
            SELECT id FROM account_move
             WHERE company_id = %(company)s
               AND move_type = 'out_invoice'
               AND state = 'posted'
               AND payment_state IN ('not_paid', 'partial')
               AND (upper(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) = %(ref)s
                    OR upper(regexp_replace(COALESCE(payment_reference, ''),
                                            '[^a-zA-Z0-9]', '', 'g')) = %(ref)s)
        """, {"company": self.company_id.id, "ref": ref})
        return self.env["account.move"].browse([row[0] for row in self.env.cr.fetchall()])

    def _partner_by_reference(self, ref):
        self.env.cr.execute("""
            SELECT id FROM res_partner
             WHERE active
               AND (company_id IS NULL OR company_id = %(company)s)
               AND (upper(regexp_replace(COALESCE(mpesa_account_ref, ''),
                                         '[^a-zA-Z0-9]', '', 'g')) = %(ref)s
                    OR upper(regexp_replace(COALESCE(ref, ''), '[^a-zA-Z0-9]', '', 'g')) = %(ref)s)
        """, {"company": self.company_id.id, "ref": ref})
        partners = self.env["res.partner"].browse([row[0] for row in self.env.cr.fetchall()])
        commercial = partners.commercial_partner_id
        return commercial if len(commercial) == 1 else self.env["res.partner"]

    def _open_invoices(self, partner):
        return self.env["account.move"].sudo().search([
            ("company_id", "=", self.company_id.id),
            ("commercial_partner_id", "=", partner.commercial_partner_id.id),
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
        ], order="invoice_date_due asc, invoice_date asc, id asc")

    # ------------------------------------------------------------------
    # Reconciliation through account_reconcile_oca
    # ------------------------------------------------------------------

    def _receivable_lines(self, moves):
        return moves.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
            and not line.reconciled and line.parent_state == "posted"
        ).sorted(lambda line: (line.date_maturity or line.date, line.id))

    def _allocate_to_invoices(self, invoices, rest="partner", partner=None):
        """Pay ``invoices`` (oldest first) from this receipt.

        ``rest`` decides what happens to money left over: ``"partner"`` leaves
        it on the customer's account as a credit, ``"suspense"`` leaves the
        line unreconciled. A difference under one shilling against the last
        invoice is written off to the rounding account: M-PESA only takes
        whole shillings.
        """
        self.ensure_one()
        lines = self._receivable_lines(invoices.sudo())
        if not lines:
            return False
        partner = partner or invoices.commercial_partner_id[:1]
        return self._reconcile_statement_line(lines, partner=partner, rest=rest,
                                              round_off=True)

    def _allocate_on_account(self, partner):
        """No invoice to pay: the whole receipt is a credit on the customer."""
        self.ensure_one()
        return self._reconcile_statement_line(
            self.env["account.move.line"], partner=partner, rest="partner")

    def _reconcile_statement_line(self, counterparts, partner=None, rest="suspense",
                                  round_off=False):
        """Reconcile this receipt's statement line against ``counterparts``.

        Each counterpart takes what is left of the receipt, up to its own
        residual, in the order given. Returns True when the line ended up
        fully reconciled.
        """
        self.ensure_one()
        st_line = self.statement_line_id.sudo()
        if not st_line:
            return False
        if st_line.is_reconciled:
            return True
        currency = self.company_id.currency_id
        if partner:
            partner = partner.commercial_partner_id
            if st_line.partner_id != partner:
                st_line.partner_id = partner
            if not self.partner_id:
                self.partner_id = partner
        data_info = st_line._default_reconcile_data()
        for line in counterparts:
            pending = currency.round(sum(
                item["amount"] for item in data_info["data"] if item["kind"] != "suspense"))
            if currency.compare_amounts(pending, 0) <= 0:
                break
            residual = currency.round(abs(line.amount_residual))
            if currency.is_zero(residual):
                continue
            # max_amount=0 means "no cap" to account_reconcile_oca: pending is
            # positive here, so the cap always is.
            cap = min(residual, pending)
            if round_off and 0 < residual - pending < 1:
                # Less than a shilling short of this invoice: close it, and
                # write the difference off as whole-shilling rounding below.
                cap = residual
            aux, new_lines = st_line._get_reconcile_line(
                line, "other", is_counterpart=True, max_amount=cap, move=True,
                reconcile_auxiliary_id=data_info["reconcile_auxiliary_id"])
            # The suspense line stays in: _recompute_suspense_line resizes it.
            data_info = st_line._recompute_suspense_line(
                data_info["data"] + new_lines, aux, data_info.get("manual_reference"))
        data_info = self._settle_remainder(st_line, data_info, partner, rest, round_off)
        if not data_info.get("can_reconcile"):
            st_line.reconcile_data = False
            return False
        st_line.reconcile_data_info = data_info
        st_line.reconcile_bank_line()
        return True

    def _settle_remainder(self, st_line, data_info, partner, rest, round_off):
        """Decide where the part of the receipt no invoice took goes."""
        currency = self.company_id.currency_id
        suspense = [item for item in data_info["data"] if item["kind"] == "suspense"]
        if not suspense:
            return data_info
        # The suspense line balances the entry: negative (a credit) is money
        # no invoice took, positive (a debit) is an invoice paid beyond it.
        remainder = currency.round(suspense[0]["amount"])
        others = [item for item in data_info["data"] if item["kind"] != "suspense"]
        if currency.is_zero(remainder):
            return data_info
        if round_off and abs(remainder) < 1:
            account = self.company_id._mpesa_rounding_account()
            if account:
                line = dict(suspense[0], kind="other", account_id=[account.id, account.display_name],
                            name=_("M-PESA whole-shilling rounding"),
                            reference=f"reconcile_auxiliary;{data_info['reconcile_auxiliary_id']}")
                return st_line._recompute_suspense_line(
                    others + [line], data_info["reconcile_auxiliary_id"] + 1,
                    data_info.get("manual_reference"))
        if rest == "partner" and partner and float_compare(
                remainder, 0, precision_rounding=currency.rounding) < 0:
            # Dropping the suspense line makes account_reconcile_oca put the
            # rest on the partner's receivable: a credit on the customer.
            return st_line._recompute_suspense_line(
                others, data_info["reconcile_auxiliary_id"], data_info.get("manual_reference"))
        return data_info

    # ------------------------------------------------------------------
    # Allocation by hand (the queue)
    # ------------------------------------------------------------------

    def _check_officer(self):
        if not self.env.user.has_group("x_ke_mpesa.group_mpesa_officer"):
            raise UserError(_("Only an M-Pesa officer can allocate receipts."))

    def _allocate_manual(self, invoices=None, partner=None, move_lines=None):
        """Allocate from the queue: invoices, a customer on account, or lines."""
        self._check_officer()
        self.ensure_one()
        if self.allocation_state not in ("unallocated", "parked"):
            raise UserError(_("Receipt %s is not waiting for allocation.", self.name))
        if self.park_reason:
            self.park_reason = False
        if invoices:
            done = self._allocate_to_invoices(invoices, rest="partner",
                                              partner=partner or invoices.commercial_partner_id[:1])
        elif move_lines:
            done = self._reconcile_statement_line(move_lines, partner=partner, rest="partner")
        elif partner:
            done = self._allocate_on_account(partner)
        else:
            raise UserError(_("Choose an invoice, a customer or a document."))
        if not done:
            raise UserError(_("Receipt %s could not be fully allocated; nothing was "
                              "changed.", self.name))
        self.message_post(body=_("Allocated by %s.", self.env.user.name))
        return True

    def action_park(self, reason):
        self._check_officer()
        if not reason:
            raise UserError(_("Say why the receipt is parked."))
        for tx in self:
            if tx.allocation_state != "unallocated":
                raise UserError(_("Only unallocated receipts can be parked."))
            tx.park_reason = reason
            tx.message_post(body=_("Parked in M-Pesa suspense: %s", reason))
        return True

    def action_unpark(self):
        self._check_officer()
        for tx in self.filtered("park_reason"):
            tx.park_reason = False
            tx.message_post(body=_("Back in the queue."))
        return True

    def action_open_allocate(self):
        self._check_officer()
        return {
            "type": "ir.actions.act_window",
            "res_model": "mpesa.allocate",
            "view_mode": "form",
            "target": "new",
            "context": {"default_transaction_ids": self.ids},
        }

    def action_open_statement_line(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.bank.statement.line",
            "res_id": self.statement_line_id.id,
            "view_mode": "form",
        }


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    mpesa_transaction_id = fields.Many2one("mpesa.transaction", "M-Pesa receipt", copy=False,
                                           index=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get("skip_mpesa_hook"):
            lines._mpesa_link_imported()
        return lines

    def _mpesa_link_imported(self):
        """A statement line that arrived by file or Pull import for an M-Pesa
        journal: make sure the receipt exists once in the log."""
        for line in self.filtered(lambda l: RECEIPT_UID_RE.fullmatch(l.unique_import_id or "")
                                  and not l.mpesa_transaction_id
                                  and l.journal_id.mpesa_shortcode_ids
                                  and l.amount > 0):
            receipt = RECEIPT_UID_RE.fullmatch(line.unique_import_id).group(1)
            shortcode = line.journal_id.mpesa_shortcode_ids[:1]
            Tx = self.env["mpesa.transaction"].sudo()
            Tx._lock("receipt", shortcode.company_id.id, receipt)
            tx = Tx.search([("company_id", "=", shortcode.company_id.id),
                            ("receipt", "=", receipt)], limit=1)
            if tx and not tx.statement_line_id:
                tx.statement_line_id = line
                line.mpesa_transaction_id = tx
                tx._auto_match()
            elif not tx:
                moment = fields.Datetime.to_datetime(line.date)
                vals = line.transaction_details if isinstance(line.transaction_details, dict) else {}
                tx = Tx.create({
                    "company_id": shortcode.company_id.id,
                    "shortcode_id": shortcode.id,
                    "source": vals.get("mpesa_source") or "statement",
                    "state": "confirmed",
                    "receipt": receipt,
                    "amount": line.amount,
                    "trans_time": vals.get("mpesa_trans_time") or moment,
                    "bill_ref": vals.get("mpesa_bill_ref") or False,
                    "customer_name": line.partner_name or False,
                    "transaction_type": line.transaction_type or False,
                    "confirmed_at": fields.Datetime.now(),
                    "statement_line_id": line.id,
                    "user_id": False,
                })
                line.mpesa_transaction_id = tx
                tx._auto_match()


class AccountBankStatement(models.Model):
    _inherit = "account.bank.statement"

    def _mpesa_sync_balance(self):
        """Our daily statements have no bank-given closing balance: it is
        whatever the lines add up to, so they stay complete as lines arrive."""
        for statement in self:
            statement.balance_end_real = statement.balance_end
