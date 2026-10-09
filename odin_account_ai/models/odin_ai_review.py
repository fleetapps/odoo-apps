"""Transaction Review: proposed accounts for what is not categorised yet.

Two kinds of items:
* bank statement lines whose counterpart is still the journal's suspense
  account (``account.bank.statement.line._seek_for_lines``);
* lines of draft vendor bills still on the purchase journal's default
  account.

Each one gets a suggestion, in this order: the accountant's own rules ("this
partner always goes to Software"), then a check for an open invoice or bill
the payment settles, or a transfer between the company's own bank accounts
(those are reconciled in the bank screen, never booked to P&L, or they would
be counted twice), then Claude, in batches, with the partner's history as
evidence. Nothing is booked until an accountant accepts.

Applying a bank suggestion rebuilds the statement line's entry exactly as
Odoo's own ``action_undo_reconciliation`` does
(addons/account/models/account_bank_statement_line.py), with the chosen
account as counterpart instead of suspense. The entry of a statement line is
always posted, so it is changed only after acceptance, only while it is
untouched since the suggestion was made, and never across a lock date.

The nightly job reports its progress with ``ir.cron._commit_progress`` so
Odoo's cron runner calls it again while items remain
(https://www.odoo.com/documentation/19.0/developer/reference/backend/actions.html#scheduled-actions).
"""

import hashlib
import logging
import re
from collections import Counter
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command, Domain
from odoo.tools.misc import formatLang

from . import odin_ai_prompts as prompts
from .odin_ai_llm import final_json
from .odin_ai_tools import dumps

_logger = logging.getLogger(__name__)

BATCH = 20
SCAN_LIMIT = 200
PL_TYPES = ("income", "income_other", "expense", "expense_other", "expense_depreciation", "expense_direct_cost")
OPEN_STATES = ("proposed", "needs_reconcile")
HISTORY_DAYS = 365
TRANSFER_DAYS = 3


class OdinAiRule(models.Model):
    _name = "odin.ai.rule"
    _description = "Categorisation rule"
    _order = "hits desc, id"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    partner_id = fields.Many2one("res.partner", required=True, index=True,
                                 domain="[('parent_id', '=', False)]")
    direction = fields.Selection([("in", "Money in"), ("out", "Money out"), ("both", "Both")],
                                 required=True, default="both")
    account_id = fields.Many2one("account.account", required=True,
                                 domain="[('account_type', 'in', %s)]" % (list(PL_TYPES),))
    analytic_distribution = fields.Json()
    hits = fields.Integer(readonly=True)
    active = fields.Boolean(default=True)

    @api.model
    def _match(self, company, partner, amount):
        if not partner:
            return self
        direction = "in" if amount > 0 else "out"
        return self.search([
            ("company_id", "=", company.id), ("partner_id", "=", partner.commercial_partner_id.id),
            ("direction", "in", (direction, "both")),
        ], limit=1)


class OdinAiSuggestion(models.Model):
    _name = "odin.ai.suggestion"
    _description = "Suggested categorisation"
    _order = "confidence desc, date desc, id desc"

    company_id = fields.Many2one("res.company", required=True, index=True)
    source = fields.Selection([("bank", "Bank line"), ("bill", "Bill line")], required=True)
    st_line_id = fields.Many2one("account.bank.statement.line", ondelete="cascade", index=True)
    aml_id = fields.Many2one("account.move.line", ondelete="cascade", index=True)
    move_id = fields.Many2one("account.move", compute="_compute_move", store=True)
    date = fields.Date()
    label = fields.Char()
    amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one("res.currency")
    partner_id = fields.Many2one("res.partner", string="Partner on the line")
    proposed_account_id = fields.Many2one("account.account")
    proposed_partner_id = fields.Many2one("res.partner")
    proposed_analytic = fields.Json()
    confidence = fields.Float()
    band = fields.Selection([("high", "High"), ("medium", "Medium"), ("low", "Low")],
                            compute="_compute_band", store=True)
    rationale = fields.Text()
    evidence_aml_ids = fields.Many2many("account.move.line", string="Similar past items")
    origin = fields.Selection([("rule", "Your rule"), ("match", "Open item"), ("ai", "AI")])
    match_move_id = fields.Many2one("account.move", string="Settles")
    fingerprint = fields.Char()
    state = fields.Selection([
        ("proposed", "To review"),
        ("needs_reconcile", "Reconcile instead"),
        ("applied", "Applied"),
        ("rejected", "Rejected"),
        ("stale", "Changed since"),
    ], default="proposed", required=True, index=True)
    reviewer_id = fields.Many2one("res.users", readonly=True)
    error = fields.Char()

    @api.depends("st_line_id", "aml_id")
    def _compute_move(self):
        for suggestion in self:
            suggestion.move_id = suggestion.st_line_id.move_id or suggestion.aml_id.move_id

    @api.depends("confidence", "proposed_account_id")
    def _compute_band(self):
        for suggestion in self:
            confidence = suggestion.confidence if suggestion.proposed_account_id else 0.0
            suggestion.band = "high" if confidence >= 0.85 else ("medium" if confidence >= 0.6 else "low")

    # ------------------------------------------------------------------
    # The inbox (called by the Transaction Review page)
    # ------------------------------------------------------------------

    @api.model
    def inbox(self):
        self._check_reader()
        suggestions = self.search([("company_id", "in", self.env.companies.ids), ("state", "in", OPEN_STATES)],
                                  limit=500)
        suggestions -= suggestions._retire_handled()
        return {
            "items": [suggestion._card() for suggestion in suggestions],
            "counts": dict(Counter(suggestions.mapped("band"))),
            "pending": len(self._pending_items(self.env.company)),
            "can_apply": self.env.user.has_group("account.group_account_user"),
            "ai": self.env["odin.ai.llm"].readiness() or False,
        }

    def _retire_handled(self):
        """Open suggestions whose item was dealt with elsewhere (a bank line
        reconciled in the bank screen, a bill posted or recategorised): they
        leave the inbox. Returns them."""
        handled = self.filtered(lambda s: (
            s.source == "bank" and s.st_line_id.is_reconciled
        ) or (
            s.source == "bill" and (s.aml_id.parent_state != "draft"
                                    or s.aml_id.account_id != s.aml_id.move_id.journal_id.default_account_id)
        ))
        if handled:
            handled.sudo().write({"state": "stale", "error": _("Handled outside Transaction Review.")})
        return handled

    def _card(self):
        self.ensure_one()
        return {
            "id": self.id,
            "source": self.source,
            "date": fields.Date.to_string(self.date) if self.date else False,
            "label": self.label or "",
            "amount": self.amount,
            "currency": self.currency_id.name or "",
            "partner": self.partner_id.display_name or "",
            "account": {"id": self.proposed_account_id.id, "name": self.proposed_account_id.display_name}
            if self.proposed_account_id else False,
            "proposed_partner": {"id": self.proposed_partner_id.id, "name": self.proposed_partner_id.display_name}
            if self.proposed_partner_id else False,
            "analytic": self._analytic_names(),
            "confidence": round(self.confidence * 100),
            "band": self.band,
            "rationale": self.rationale or "",
            "origin": self.origin,
            "state": self.state,
            "error": self.error or "",
            "match": {"id": self.match_move_id.id, "name": self.match_move_id.display_name}
            if self.match_move_id else False,
            "move_id": self.move_id.id,
            "evidence": [
                {"date": fields.Date.to_string(line.date), "entry": line.move_name,
                 "account": line.account_id.display_name, "label": line.name or "",
                 "amount": line.balance, "move_id": line.move_id.id}
                for line in self.evidence_aml_ids[:5]
            ],
        }

    def _analytic_names(self):
        names = []
        for key, pct in (self.proposed_analytic or {}).items():
            accounts = self.env["account.analytic.account"].browse(
                [int(i) for i in key.split(",")]).exists()
            if accounts:
                names.append(f"{' / '.join(accounts.mapped('name'))} {pct:g}%")
        return names

    def action_accept(self, account_id=False, partner_id=False, remember=False):
        """Book the items as proposed, or to the account the reviewer chose."""
        self._check_accountant()
        account = partner = None
        if account_id:
            account = self.env["account.account"].browse(int(account_id)).exists()
            if not account or account.account_type not in PL_TYPES:
                raise UserError(_("Pick an income or expense account."))
        if partner_id:
            partner = self.env["res.partner"].browse(int(partner_id)).exists()
        results = []
        for suggestion in self:
            if suggestion.state == "proposed":
                if account:
                    suggestion.proposed_account_id = account
                if partner:
                    suggestion.proposed_partner_id = partner
            result = suggestion._apply()
            if remember and suggestion.state == "applied":
                suggestion._remember()
            results.append(result)
        return results

    def action_reject(self):
        self._check_accountant()
        self.filtered(lambda s: s.state in OPEN_STATES).write({"state": "rejected", "reviewer_id": self.env.uid})
        return True

    def action_open_document(self):
        self.ensure_one()
        self._check_reader()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": self.move_id.id,
                "views": [(False, "form")], "target": "current"}

    def _remember(self):
        """'Always do this': a rule for the partner and direction."""
        partner = (self.proposed_partner_id or self.partner_id).commercial_partner_id
        if not partner:
            return
        Rule = self.env["odin.ai.rule"]
        direction = "in" if self.amount > 0 else "out"
        rule = Rule.search([("company_id", "=", self.company_id.id), ("partner_id", "=", partner.id),
                            ("direction", "=", direction)], limit=1)
        values = {"account_id": self.proposed_account_id.id, "analytic_distribution": self.proposed_analytic}
        if rule:
            rule.write(values)
        else:
            Rule.create(dict(values, company_id=self.company_id.id, partner_id=partner.id, direction=direction))

    # ------------------------------------------------------------------
    # Applying
    # ------------------------------------------------------------------

    def _apply(self):
        self.ensure_one()
        if self.state != "proposed":
            return {"id": self.id, "state": self.state, "error": _("This suggestion is not open any more.")}
        if not self.proposed_account_id:
            return {"id": self.id, "state": self.state, "error": _("Pick an account first.")}
        try:
            with self.env.cr.savepoint():
                if self.source == "bank":
                    self._apply_bank()
                else:
                    self._apply_bill()
        except _Stale as error:
            self.write({"state": "stale", "error": str(error)[:250]})
            return {"id": self.id, "state": self.state, "error": self.error}
        except UserError as error:
            # Lock dates and other accounting checks: shown on the card; the
            # suggestion stays open so it can be retried or rejected.
            self.error = str(error)[:250]
            return {"id": self.id, "state": self.state, "error": self.error}
        self.write({"state": "applied", "reviewer_id": self.env.uid, "error": False})
        if self.origin == "rule":
            rule = self.env["odin.ai.rule"]._match(
                self.company_id, self.proposed_partner_id or self.partner_id, self.amount)
            if rule.account_id == self.proposed_account_id:
                rule.sudo().hits += 1
        return {"id": self.id, "state": self.state, "error": False}

    def _apply_bank(self):
        st_line = self.st_line_id.try_lock_for_update()
        if not st_line:
            raise _Stale(_("Someone is working on this bank line right now."))
        problem = self._bank_problem(st_line)
        if problem:
            raise _Stale(problem)
        st_line.move_id._check_fiscal_lock_dates()
        if self.proposed_partner_id and self.proposed_partner_id != st_line.partner_id:
            # The rebuilt lines copy the statement line's partner, so it goes first.
            st_line.partner_id = self.proposed_partner_id
        values = st_line._prepare_move_line_default_vals(counterpart_account_id=self.proposed_account_id.id)
        if self.proposed_analytic:
            values[1]["analytic_distribution"] = self.proposed_analytic
        st_line.with_context(force_delete=True, skip_readonly_check=True).write({
            "checked": st_line.move_id._is_user_able_to_review(),
            "line_ids": [Command.clear()] + [Command.create(vals) for vals in values],
        })
        if "reconcile_data" in st_line._fields:
            # account_reconcile_oca keeps the reconcile widget's draft here.
            st_line.reconcile_data = False
        st_line.move_id.message_post(body=_(
            "Categorised to %(account)s from a Transaction Review suggestion, approved by %(user)s.",
            account=self.proposed_account_id.display_name, user=self.env.user.name))

    def _bank_problem(self, st_line):
        """Why a bank line cannot take a suggestion now, or False."""
        if st_line.is_reconciled:
            return _("The bank line has been reconciled since.")
        if st_line.move_id.inalterable_hash:
            return _("The bank line's entry is secured (hashed) and cannot change.")
        liquidity, suspense, other = st_line._seek_for_lines()
        if len(liquidity) != 1 or len(suspense) != 1 or other:
            return _("The bank line's entry has been changed since.")
        if self.fingerprint and self.fingerprint != _fingerprint(st_line):
            return _("The bank line has been edited since the suggestion was made.")
        if "reconcile_data" in st_line._fields and st_line.reconcile_data:
            return _("Someone started reconciling this line in the bank screen.")
        if "reconcile_mode" in st_line.journal_id._fields and st_line.journal_id.reconcile_mode == "keep":
            return _("This journal keeps suspense lines (reconcile mode 'keep').")
        return False

    def _apply_bill(self):
        line = self.aml_id
        if not line.exists() or line.parent_state != "draft":
            raise _Stale(_("The bill is no longer a draft."))
        if self.fingerprint and self.fingerprint != _fingerprint(line):
            raise _Stale(_("The bill line has been edited since the suggestion was made."))
        values = {"account_id": self.proposed_account_id.id}
        if self.proposed_analytic:
            values["analytic_distribution"] = self.proposed_analytic
        line.write(values)
        line.move_id.message_post(body=_(
            "Line “%(label)s” categorised to %(account)s from a Transaction Review suggestion, "
            "approved by %(user)s.",
            label=line.name or "", account=self.proposed_account_id.display_name, user=self.env.user.name))

    # ------------------------------------------------------------------
    # Generating
    # ------------------------------------------------------------------

    @api.model
    def action_generate(self):
        """From the page: rules and open items for everything, then one
        batch for Claude. The page calls again while items remain.
        Accountants only: it spends the AI budget and makes suggestions."""
        self._check_reader()
        self._check_accountant()
        self._generate(self.env.company, interactive=True)
        return self.inbox()

    @api.model
    def _cron_generate(self):
        """One batch per company with consent per call; Odoo's cron runner
        calls again while ``remaining`` is not zero."""
        processed = remaining = 0
        for company in self.env["res.company"].search([("odin_ai_consent", "=", True)]):
            done, left = self.with_company(company).with_context(
                allowed_company_ids=[company.id])._generate(company, interactive=False)
            processed += done
            remaining += left
        self.env["ir.cron"]._commit_progress(processed, remaining=remaining)

    @api.model
    def _generate(self, company, interactive):
        """Returns (items handled, items left for the next call)."""
        handled = 0
        to_ask = []
        for item in self._pending_items(company):
            # An exact match with an open invoice beats a rule; a rule (the
            # accountant's own decision) beats the partner's open items.
            if self._from_open_item(company, item) or self._from_rule(company, item) \
                    or self._from_open_partner(company, item):
                handled += 1
            else:
                to_ask.append(item)
        if not to_ask or self.env["odin.ai.llm"].readiness():
            return handled, 0
        batch, rest = to_ask[:BATCH], to_ask[BATCH:]
        if not self._ask_batch(company, batch, interactive):
            # The API failed: stop here, the items stay pending for next time.
            return handled, 0
        return handled + len(batch), len(rest)

    @api.model
    def _pending_items(self, company):
        """Bank lines on suspense and draft bill lines on the default
        account, without a suggestion still open or waiting."""
        taken = self.search([("company_id", "=", company.id), ("state", "in", OPEN_STATES + ("rejected",))])
        items = []
        st_lines = self.env["account.bank.statement.line"].search([
            ("company_id", "=", company.id), ("is_reconciled", "=", False),
            ("id", "not in", taken.st_line_id.ids),
        ], order="date desc, id desc", limit=SCAN_LIMIT)
        for st_line in st_lines:
            liquidity, suspense, other = st_line._seek_for_lines()
            if len(liquidity) != 1 or len(suspense) != 1 or other:
                continue
            # account_reconcile_oca writes reconcile_data on every line as it
            # is created (its create() calls _auto_reconcile), so the field
            # being set says nothing about whether anyone has worked on it --
            # and skipping on that alone hid every bank line there will ever be
            # on a database carrying that module. What means hands off is a
            # counterpart having been proposed, which is the same "other" that
            # _seek_for_lines reports; liquidity and suspense are the line's
            # own two rows.
            proposal = (st_line.reconcile_data or {}).get("data") or []
            if any(row.get("kind") == "other" for row in proposal):
                continue
            items.append({
                "source": "bank", "record": st_line, "date": st_line.date, "label": st_line.payment_ref or "",
                "amount": st_line.amount, "currency": st_line.currency_id, "partner": st_line.partner_id,
                "fingerprint": _fingerprint(st_line), "journal": st_line.journal_id.name,
            })
        bill_lines = self.env["account.move.line"].search([
            ("company_id", "=", company.id), ("parent_state", "=", "draft"),
            ("move_id.move_type", "in", ("in_invoice", "in_refund")), ("display_type", "=", "product"),
            ("id", "not in", taken.aml_id.ids),
        ], order="date desc, id desc", limit=SCAN_LIMIT)
        for line in bill_lines.filtered(lambda l: l.account_id == l.move_id.journal_id.default_account_id):
            items.append({
                "source": "bill", "record": line, "date": line.move_id.invoice_date or line.date,
                "label": " ".join(filter(None, [line.product_id.display_name, line.name])),
                # Signed like a bank line: money out is negative.
                "amount": -line.balance, "currency": line.company_currency_id,
                "partner": line.move_id.partner_id,
                "fingerprint": _fingerprint(line), "journal": line.journal_id.name,
            })
        return items

    def _base_values(self, company, item):
        return {
            "company_id": company.id,
            "source": item["source"],
            "st_line_id": item["record"].id if item["source"] == "bank" else False,
            "aml_id": item["record"].id if item["source"] == "bill" else False,
            "date": item["date"],
            "label": item["label"][:250],
            "amount": item["amount"],
            "currency_id": item["currency"].id,
            "partner_id": item["partner"].id,
            "fingerprint": item["fingerprint"],
        }

    def _from_rule(self, company, item):
        rule = self.env["odin.ai.rule"]._match(company, item["partner"], item["amount"])
        if not rule:
            return False
        return self.create(dict(
            self._base_values(company, item),
            proposed_account_id=rule.account_id.id,
            proposed_partner_id=item["partner"].id,
            proposed_analytic=rule.analytic_distribution,
            confidence=0.99, origin="rule",
            rationale=_("Your rule for %(partner)s.", partner=item["partner"].commercial_partner_id.display_name)))

    def _from_open_item(self, company, item):
        """A bank line that pays an open invoice or bill, or that moves money
        between the company's own accounts, is reconciled, not categorised."""
        if item["source"] != "bank":
            return False
        st_line = item["record"]
        match, reason = self._open_item_match(st_line)
        if not match:
            match, reason = self._transfer_match(st_line)
        if not match:
            return False
        return self.create(dict(
            self._base_values(company, item),
            state="needs_reconcile", match_move_id=match.id, confidence=0.95, origin="match", rationale=reason))

    def _from_open_partner(self, company, item):
        """Money from a customer with unpaid invoices (or to a supplier with
        unpaid bills) is almost always a payment of some of them, even when
        no single one matches the amount. Booking it to income or expenses
        would count it twice, so it is sent to reconciliation, never to the
        AI, whatever the partner's history says."""
        if item["source"] != "bank" or not item["partner"]:
            return False
        st_line = item["record"]
        account_type = "asset_receivable" if st_line.amount > 0 else "liability_payable"
        open_lines = self.env["account.move.line"].search(
            Domain(st_line._get_default_amls_matching_domain())
            & Domain("account_id.account_type", "=", account_type)
            & Domain("partner_id", "child_of", st_line.partner_id.commercial_partner_id.id),
            order="date_maturity, id", limit=50)
        if not open_lines:
            return False
        total = abs(sum(open_lines.mapped("amount_residual")))
        what = _("unpaid invoices") if st_line.amount > 0 else _("unpaid bills")
        return self.create(dict(
            self._base_values(company, item),
            state="needs_reconcile", match_move_id=open_lines[:1].move_id.id, confidence=0.8, origin="match",
            rationale=_("%(partner)s has %(count)s %(what)s (%(total)s open, oldest %(doc)s). This is "
                        "probably a payment of some of them: reconcile it in the bank screen rather than booking "
                        "it to an account, or it would be counted twice.",
                        partner=st_line.partner_id.commercial_partner_id.display_name,
                        count=len(open_lines.move_id), what=what,
                        total=formatLang(self.env, total, currency_obj=st_line.company_id.currency_id),
                        doc=open_lines[:1].move_id.display_name)))

    def _open_item_match(self, st_line):
        AML = self.env["account.move.line"]
        currency = st_line.currency_id
        company_currency = st_line.company_id.currency_id
        amount = st_line.amount
        domain = Domain(st_line._get_default_amls_matching_domain()) \
            & Domain("account_id.account_type", "in", ("asset_receivable", "liability_payable")) \
            & (Domain("amount_residual_currency", "=", amount) | Domain("amount_residual", "=", amount))
        if st_line.partner_id:
            domain &= Domain("partner_id", "child_of", st_line.partner_id.commercial_partner_id.id)

        def residual(line):
            # Receivables are open with a positive residual and are paid by
            # money in; payables the other way round, so the signs match.
            if line.currency_id == currency:
                return line.amount_residual_currency
            if currency == company_currency:
                return line.amount_residual
            return None

        candidates = AML.search(domain, limit=50).filtered(
            lambda line: residual(line) is not None and currency.is_zero(residual(line) - amount))
        if not candidates:
            return False, ""
        reference = (st_line.payment_ref or "").lower()
        by_reference = candidates.filtered(lambda line: any(
            ref and len(ref) > 2 and ref.lower() in reference for ref in (line.move_id.name, line.move_id.ref)))
        if by_reference:
            candidates = by_reference
        elif len(candidates.move_id) > 1 and not st_line.partner_id:
            return False, ""
        move = candidates[:1].move_id
        return move, _(
            "Looks like the payment of %(doc)s (same amount%(ref)s). Reconcile it in the bank screen "
            "rather than booking it to an account, or it would be counted twice.",
            doc=move.display_name, ref=_(" and reference") if by_reference else "")

    def _transfer_match(self, st_line):
        """The same amount the other way on another of the company's bank or
        cash journals within a few days."""
        other = self.env["account.bank.statement.line"].search([
            ("company_id", "=", st_line.company_id.id),
            ("journal_id", "!=", st_line.journal_id.id),
            ("is_reconciled", "=", False),
            ("amount", "=", -st_line.amount),
            ("date", ">=", st_line.date - timedelta(days=TRANSFER_DAYS)),
            ("date", "<=", st_line.date + timedelta(days=TRANSFER_DAYS)),
        ], limit=1)
        if not other or other.currency_id != st_line.currency_id:
            return False, ""
        return other.move_id, _(
            "Looks like a transfer to or from %(journal)s (%(label)s, same amount the other way). "
            "Reconcile both sides with the internal transfer account rather than booking it to P&L.",
            journal=other.journal_id.name, label=other.payment_ref or other.move_id.name)

    def _ask_batch(self, company, batch, interactive):
        """One call for up to BATCH items. Every item gets a suggestion,
        empty when the model has none, so nothing is asked twice."""
        allowed = self.env["account.account"].search([
            ("account_type", "in", PL_TYPES), ("company_ids", "in", company.ids)])
        by_code = {account.code: account for account in allowed if account.code}
        analytic = self.env["account.analytic.account"].search(
            [("company_id", "in", [False, company.id])], limit=200)
        history = self._history(company, batch)
        payload = [{
            "item": index,
            "date": fields.Date.to_string(item["date"]),
            "label": item["label"][:200],
            "amount": round(item["amount"], 2),
            "currency": item["currency"].name,
            "partner": (item["partner"].display_name or "")[:100],
            "journal": item["journal"],
            "kind": "bank line" if item["source"] == "bank" else "vendor bill line",
            "history": {key: value for key, value in history[index].items() if key != "line_ids"},
        } for index, item in enumerate(batch)]
        system = prompts.REVIEW_RULES.format(
            company=company.name,
            accounts="\n".join(f"  {code} {account.name} [{account.account_type}]"
                               for code, account in sorted(by_code.items())),
            analytic=", ".join(analytic.mapped("name")) or "none",
        )
        try:
            # Keyed on the items, so a retried request reuses the answer.
            step_key = "review-%s-%s" % (company.id, hashlib.sha1(
                "|".join(item["fingerprint"] for item in batch).encode()).hexdigest())
            data = self.env["odin.ai.llm"].call(
                "review", system=system, schema=prompts.REVIEW_SCHEMA, interactive=interactive,
                step_key=step_key,
                messages=[{"role": "user", "content": "Items to categorise (data): " + dumps(payload)}])
            answer = final_json(data)
        except UserError as error:
            _logger.warning("odin_account_ai: review batch failed: %s", error)
            return False
        analytic_by_name = {account.name.lower(): account for account in analytic}
        proposals = {}
        for proposal in answer.get("suggestions") or []:
            index = proposal.get("item")
            if isinstance(index, int) and 0 <= index < len(batch) and index not in proposals:
                proposals[index] = proposal
        values_list = []
        for index, item in enumerate(batch):
            proposal = proposals.get(index) or {}
            account = by_code.get((proposal.get("account_code") or "").strip())
            confidence = proposal.get("confidence")
            confidence = min(max(float(confidence), 0.0), 1.0) if isinstance(confidence, (int, float)) else 0.0
            if history[index].get("top_account") != (account.code if account else None):
                # The model's confidence counts for more only when the books agree.
                confidence = min(confidence, 0.7)
            partner = item["partner"]
            if not partner and proposal.get("partner"):
                partner = self.env["res.partner"].search(
                    [("name", "=ilike", proposal["partner"].strip()),
                     ("company_id", "in", [False, company.id])], limit=1)
            name = (proposal.get("analytic_account") or "").strip().lower()
            distribution = {str(analytic_by_name[name].id): 100.0} if account and name in analytic_by_name else False
            values_list.append(dict(
                self._base_values(company, item),
                proposed_account_id=account.id if account else False,
                proposed_partner_id=partner.id if partner else False,
                proposed_analytic=distribution,
                confidence=confidence if account else 0.0,
                origin="ai",
                rationale=(proposal.get("reason") or _("No suggestion: pick the account.")).strip()[:500],
                evidence_aml_ids=[Command.set(history[index]["line_ids"])],
            ))
        self.create(values_list)
        return True

    def _history(self, company, batch):
        """For each item: where this partner's past items were booked (top
        accounts with counts) and similar past labels."""
        AML = self.env["account.move.line"]
        since = fields.Date.context_today(self) - timedelta(days=HISTORY_DAYS)
        base = Domain("company_id", "=", company.id) & Domain("parent_state", "=", "posted") \
            & Domain("date", ">=", since) & Domain("account_id.account_type", "in", PL_TYPES)
        result = {}
        for index, item in enumerate(batch):
            info = {}
            line_ids = []
            partner = item["partner"].commercial_partner_id
            if partner:
                partner_domain = base & Domain("partner_id", "child_of", partner.id)
                groups = AML._read_group(partner_domain, ["account_id"], ["__count"],
                                         order="__count desc", limit=3)
                if groups:
                    info["partner_accounts"] = [{"account": f"{account.code} {account.name}", "times": count}
                                                for account, count in groups]
                    info["top_account"] = groups[0][0].code
                line_ids += AML.search(partner_domain, limit=3, order="date desc, id desc").ids
            word = _keyword(item["label"])
            if word:
                similar = AML.search(base & Domain("name", "ilike", word), limit=3, order="date desc, id desc")
                if similar:
                    info["similar_labels"] = [{"label": (line.name or "")[:80], "account": line.account_id.code}
                                              for line in similar]
                line_ids += similar.ids
            info["line_ids"] = list(dict.fromkeys(line_ids))[:5]
            result[index] = info
        return result

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    @api.model
    def _check_reader(self):
        if not self.env.user.has_group("odin_account_ai.group_ai_user") or \
                not self.env.user.has_group("account.group_account_readonly"):
            raise AccessError(_("Transaction Review is for users who can see accounting."))

    @api.model
    def _check_accountant(self):
        # account.move._post only needs Invoicing rights; booking a suggestion
        # is an accountant's decision, so it is checked here.
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only accountants can book suggestions."))


class _Stale(UserError):
    """The item changed since the suggestion was made."""


def _fingerprint(record):
    """What the suggestion was made on. Not write_date: recomputed fields
    touch it without the item changing."""
    if record._name == "account.bank.statement.line":
        parts = [record.amount, str(record.date), record.payment_ref, record.partner_id.id,
                 sorted((line.id, line.account_id.id) for line in record.move_id.line_ids)]
    else:
        parts = [record.account_id.id, record.balance, record.name, record.product_id.id,
                 record.move_id.partner_id.id]
    return hashlib.sha1(repr(parts).encode()).hexdigest()


_WORD = re.compile(r"[^\W\d_]{4,}")
_STOPWORDS = {"payment", "transfer", "invoice", "from", "with", "card", "bank", "online", "debit", "credit",
              "purchase", "mpesa", "reference"}


def _keyword(label):
    for word in _WORD.findall(label or ""):
        if word.lower() not in _STOPWORDS:
            return word
    return ""
