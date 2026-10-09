"""Anomalies: scheduled checks on the books, with an inbox of findings.

The checks are plain queries, not AI: they run every night (and on demand),
cost nothing and say exactly why they fired. Claude is only asked, on a
finding the accountant opens, to suggest the likely cause and the next step.

Two kinds of check:
* condition: true while the problem exists (two bills that look the same,
  bank lines left unreconciled). After a run that completed, a finding whose
  condition no longer holds is resolved automatically.
* event: something that happened (an unusual amount, a backdated entry).
  It stays until someone deals with it, or its entries are cancelled.

A finding someone dismissed is never reopened by the same check. Findings
use chatter and activities, so they can be assigned and followed up like any
other task in Odoo.
https://www.odoo.com/documentation/19.0/applications/productivity/discuss/activities.html
"""

import hashlib
import json
import logging
import statistics
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain
from odoo.tools.misc import formatLang

from . import odin_ai_prompts as prompts
from .odin_ai_llm import final_json
from .odin_ai_tools import dumps

_logger = logging.getLogger(__name__)

SEVERITY = [("high", "High"), ("medium", "Medium"), ("low", "Low")]
PRIORITY = {"high": 0, "medium": 1, "low": 2}
OPEN = ("open", "in_progress")
PL_TYPES = ("income", "income_other", "expense", "expense_other", "expense_depreciation", "expense_direct_cost")
MAX_FINDINGS_PER_CHECK = 200

# What each check reads from ``odin.ai.detector.params``, with its default.
DEFAULTS = {
    "duplicate_bill": {"days": 120, "date_gap": 3},
    "amount_outlier": {"days": 30, "history_days": 365, "min_history": 6, "threshold": 5.0},
    "new_account": {"days": 30, "history_days": 365, "min_history": 5},
    "large_manual": {"days": 30, "min_amount": 0.0},
    "backdated": {"days": 30, "max_gap": 30},
    "missing_analytic": {"days": 30, "min_share": 0.5},
    "stale_bank": {"days": 30, "high_days": 90},
}


class OdinAiDetector(models.Model):
    _name = "odin.ai.detector"
    _description = "Anomaly check"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Selection([
        ("duplicate_bill", "Possible duplicate bills"),
        ("amount_outlier", "Unusual bill amount"),
        ("new_account", "New account for a supplier"),
        ("large_manual", "Large manual entry on cash, receivables, payables or equity"),
        ("backdated", "Backdated entry"),
        ("missing_analytic", "Income or expense without analytic"),
        ("stale_bank", "Bank lines left unreconciled"),
    ], required=True)
    kind = fields.Selection([("condition", "Condition"), ("event", "Event")], required=True)
    description = fields.Text(translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    params = fields.Text(string="Thresholds", help="Overrides of the check's thresholds as JSON, e.g. {\"days\": 60}.")
    last_run = fields.Datetime(readonly=True)
    last_status = fields.Char(readonly=True)
    finding_count = fields.Integer(compute="_compute_finding_count")

    _code_uniq = models.Constraint("UNIQUE(code)", "There is already a check of this kind.")

    def _compute_finding_count(self):
        counts = dict(self.env["odin.ai.finding"]._read_group(
            [("detector_id", "in", self.ids), ("state", "in", OPEN)], ["detector_id"], ["__count"]))
        for detector in self:
            detector.finding_count = counts.get(detector, 0)

    @api.constrains("params", "code")
    def _check_params(self):
        for detector in self.filtered("params"):
            try:
                params = json.loads(detector.params)
            except ValueError as error:
                raise ValidationError(_("Thresholds must be JSON, e.g. {\"days\": 60}.")) from error
            unknown = set(params) - set(DEFAULTS[detector.code]) if isinstance(params, dict) else {"?"}
            if unknown:
                raise ValidationError(_("This check takes %(keys)s.", keys=", ".join(DEFAULTS[detector.code])))
            if any(not isinstance(value, (int, float)) or value < 0 for value in params.values()):
                raise ValidationError(_("Thresholds are positive numbers."))

    def _param(self, name):
        params = json.loads(self.params) if self.params else {}
        return params.get(name, DEFAULTS[self.code][name])

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    @api.model
    def action_run_all(self):
        """Run now, from the Anomalies screen, for the current company."""
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only accountants can run the checks."))
        found = self.sudo().search([])._run(self.env.company)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Checks done: %(new)s new finding(s), %(resolved)s resolved.",
                             new=found["new"], resolved=found["resolved"]),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    @api.model
    def _cron_run(self):
        detectors = self.search([])
        for company in self.env["res.company"].search([]):
            detectors.with_company(company).with_context(allowed_company_ids=[company.id])._run(company)
            self.env["ir.cron"]._commit_progress(1)

    def _run(self, company):
        """Run each check; a check that fails leaves its findings as they
        were (nothing is resolved on an incomplete run)."""
        totals = {"new": 0, "resolved": 0}
        for detector in self:
            try:
                with self.env.cr.savepoint():
                    results = getattr(detector, f"_detect_{detector.code}")(company)
                    new, resolved = detector._sync(company, results[:MAX_FINDINGS_PER_CHECK])
                detector.write({"last_run": fields.Datetime.now(), "last_status": _(
                    "%(count)s finding(s)", count=len(results))})
                totals["new"] += new
                totals["resolved"] += resolved
            except Exception as error:  # noqa: BLE001 (one broken check must not stop the others)
                _logger.exception("odin_account_ai: check %s failed", detector.code)
                detector.write({"last_run": fields.Datetime.now(), "last_status": _(
                    "Failed: %(error)s", error=str(error)[:200])})
        return totals

    def _sync(self, company, results):
        """Create new findings, refresh known ones, resolve what is gone."""
        Finding = self.env["odin.ai.finding"].sudo()
        existing = {finding.fingerprint: finding for finding in Finding.with_context(active_test=False).search(
            [("company_id", "=", company.id), ("detector_id", "=", self.id)])}
        now = fields.Datetime.now()
        new = resolved = 0
        seen = set()
        for result in results:
            seen.add(result["fingerprint"])
            values = {
                "name": result["name"],
                "severity": result["severity"],
                "amount": result.get("amount") or 0.0,
                "date": result.get("date"),
                "details": result["details"],
                "facts": result.get("facts") or {},
                "move_ids": [fields.Command.set(result.get("moves", self.env["account.move"]).ids)],
                "last_seen": now,
            }
            finding = existing.get(result["fingerprint"])
            if not finding:
                Finding.create(dict(values, company_id=company.id, detector_id=self.id,
                                    fingerprint=result["fingerprint"]))
                new += 1
            elif finding.state == "dismissed":
                finding.last_seen = now
            elif finding.state in ("resolved", "auto_resolved") and self.kind == "condition":
                finding.write(dict(values, state="open"))
                finding.message_post(body=_("Found again by the nightly checks."))
                new += 1
            elif finding.state in OPEN:
                finding.write(values)
        for fingerprint, finding in existing.items():
            if finding.state not in OPEN:
                continue
            gone = fingerprint not in seen if self.kind == "condition" else all(
                move.state == "cancel" for move in finding.move_ids)
            if gone:
                finding.write({"state": "auto_resolved", "resolved_date": now})
                finding.message_post(body=_("Resolved: the check no longer finds this."))
                resolved += 1
        return new, resolved

    # ------------------------------------------------------------------
    # The checks. Each returns a list of dicts: fingerprint, name,
    # severity, details, amount, date, moves, facts.
    # ------------------------------------------------------------------

    def _money(self, company, amount):
        return formatLang(self.env, amount, currency_obj=company.currency_id)

    def _since(self, days):
        return fields.Date.context_today(self) - timedelta(days=int(days))

    def _detect_duplicate_bill(self, company):
        Move = self.env["account.move"]
        bills = Move.search([
            ("company_id", "=", company.id), ("move_type", "in", ("in_invoice", "in_refund")),
            ("state", "in", ("draft", "posted")),
            "|", ("invoice_date", ">=", self._since(self._param("days"))),
            "&", ("invoice_date", "=", False), ("create_date", ">=", self._since(self._param("days"))),
        ])
        pairs = {}
        # Odoo's own check (same reference in the same year, or same partner,
        # amount and date), as shown on the bill form.
        for bill, duplicates in bills._fetch_duplicate_reference(matching_states=("draft", "posted")).items():
            for duplicate in duplicates:
                pairs.setdefault(tuple(sorted((bill.id, duplicate.id))), _("the same supplier reference"))
        # Same supplier and amount a few days apart, which Odoo's check misses.
        gap = int(self._param("date_gap"))
        groups = defaultdict(list)
        for bill in bills.filtered(lambda b: b.invoice_date and b.amount_total):
            groups[(bill.commercial_partner_id.id, bill.currency_id.id, bill.move_type, bill.amount_total)].append(bill)
        for group in groups.values():
            group.sort(key=lambda b: b.invoice_date)
            for index, bill in enumerate(group):
                for other in group[index + 1:]:
                    if (other.invoice_date - bill.invoice_date).days > gap:
                        break
                    pairs.setdefault(tuple(sorted((bill.id, other.id))),
                                     _("the same supplier and amount, %(days)s day(s) apart",
                                       days=(other.invoice_date - bill.invoice_date).days))
        results = []
        for (first_id, second_id), reason in pairs.items():
            moves = Move.browse([first_id, second_id])
            if any(move.state not in ("draft", "posted") for move in moves):
                continue
            first, second = moves
            results.append({
                "fingerprint": f"dup:{first_id}:{second_id}",
                "name": _("%(a)s and %(b)s may be the same bill", a=first.display_name, b=second.display_name),
                "severity": "high" if all(move.state == "posted" for move in moves) else "medium",
                "amount": abs(first.amount_total_signed),
                "date": first.invoice_date or first.date,
                "moves": moves,
                "details": _("Both bills have %(reason)s (%(partner)s, %(amount)s).",
                             reason=reason, partner=first.partner_id.display_name,
                             amount=formatLang(self.env, first.amount_total, currency_obj=first.currency_id)),
            })
        return results

    def _detect_amount_outlier(self, company):
        """A bill far above what this supplier usually bills: robust z-score
        on the median and median absolute deviation of its past bills."""
        Move = self.env["account.move"]
        since = self._since(self._param("days"))
        recent = Move.search([
            ("company_id", "=", company.id), ("move_type", "=", "in_invoice"), ("state", "=", "posted"),
            ("date", ">=", since), ("commercial_partner_id", "!=", False),
        ])
        results = []
        history_since = since - timedelta(days=int(self._param("history_days")))
        for partner, bills in _group_by(recent, lambda b: b.commercial_partner_id).items():
            past = Move.search([
                ("company_id", "=", company.id), ("move_type", "=", "in_invoice"), ("state", "=", "posted"),
                ("commercial_partner_id", "=", partner.id), ("date", ">=", history_since), ("date", "<", since),
            ])
            amounts = [abs(bill.amount_total_signed) for bill in past]
            if len(amounts) < int(self._param("min_history")):
                continue
            median = statistics.median(amounts)
            mad = statistics.median(abs(amount - median) for amount in amounts)
            for bill in bills:
                amount = abs(bill.amount_total_signed)
                if mad:
                    score = 0.6745 * (amount - median) / mad
                    unusual = score > float(self._param("threshold")) and amount > 2 * median
                else:
                    score = None
                    unusual = amount > 3 * median
                if not unusual:
                    continue
                results.append({
                    "fingerprint": f"outlier:{bill.id}",
                    "name": _("%(bill)s is %(times)s× what %(partner)s usually bills",
                              bill=bill.display_name, times=round(amount / median, 1) if median else "∞",
                              partner=partner.display_name),
                    "severity": "high" if median and amount > 5 * median else "medium",
                    "amount": amount,
                    "date": bill.invoice_date or bill.date,
                    "moves": bill,
                    "details": _("%(amount)s against a usual %(median)s (median of %(count)s bills over the "
                                 "previous year).", amount=self._money(company, amount),
                                 median=self._money(company, median), count=len(amounts)),
                    "facts": {"median": median, "mad": mad, "robust_z": score, "history": len(amounts)},
                })
        return results

    def _detect_new_account(self, company):
        """A bill line on an account this supplier's bills never used."""
        AML = self.env["account.move.line"]
        since = self._since(self._param("days"))
        lines = AML.search([
            ("company_id", "=", company.id), ("parent_state", "=", "posted"),
            ("move_id.move_type", "=", "in_invoice"), ("display_type", "=", "product"),
            ("date", ">=", since), ("move_id.commercial_partner_id", "!=", False),
        ])
        history_since = since - timedelta(days=int(self._param("history_days")))
        results = []
        by_partner = _group_by(lines, lambda line: line.move_id.commercial_partner_id)
        for partner, partner_lines in by_partner.items():
            past = AML._read_group([
                ("company_id", "=", company.id), ("parent_state", "=", "posted"),
                ("move_id.move_type", "=", "in_invoice"), ("display_type", "=", "product"),
                ("move_id.commercial_partner_id", "=", partner.id),
                ("date", ">=", history_since), ("date", "<", since),
            ], ["account_id"], ["__count"])
            if sum(count for _account, count in past) < int(self._param("min_history")):
                continue
            usual = {account for account, _count in past}
            for (move, account), group in _group_by(partner_lines, lambda l: (l.move_id, l.account_id)).items():
                if account in usual:
                    continue
                top = max(past, key=lambda row: row[1])[0]
                results.append({
                    "fingerprint": f"newacct:{move.id}:{account.id}",
                    "name": _("%(bill)s books %(partner)s to %(account)s for the first time",
                              bill=move.display_name, partner=partner.display_name, account=account.display_name),
                    "severity": "low",
                    "amount": abs(sum(group.mapped("balance"))),
                    "date": move.invoice_date or move.date,
                    "moves": move,
                    "details": _("Over the previous year this supplier's bills went to %(accounts)s, mostly "
                                 "%(top)s.", accounts=", ".join(a.code for a in usual if a.code),
                                 top=top.display_name),
                })
        return results

    def _detect_large_manual(self, company):
        """Manual entries moving large amounts on cash, receivables, payables
        or equity: where errors and fraud hurt most."""
        AML = self.env["account.move.line"]
        sensitive = ("asset_cash", "asset_receivable", "liability_payable", "equity", "equity_unaffected")
        manual = Domain("company_id", "=", company.id) & Domain("parent_state", "=", "posted") \
            & Domain("move_id.move_type", "=", "entry") & Domain("journal_id.type", "=", "general") \
            & Domain("move_id.statement_line_id", "=", False) & Domain("move_id.origin_payment_id", "=", False) \
            & Domain("account_id.account_type", "in", sensitive)
        threshold = float(self._param("min_amount"))
        if not threshold:
            # By default: the 95th percentile of such amounts over the past year.
            past = AML.search(manual & Domain("date", ">=", self._since(365)), limit=5000).mapped(
                lambda line: abs(line.balance))
            if len(past) < 20:
                return []
            threshold = statistics.quantiles(past, n=20)[-1]
        lines = AML.search(manual & Domain("date", ">=", self._since(self._param("days")))).filtered(
            lambda line: abs(line.balance) >= threshold)
        results = []
        for move, group in _group_by(lines, lambda line: line.move_id).items():
            equity = any(line.account_id.account_type.startswith("equity") for line in group)
            amount = max(abs(line.balance) for line in group)
            results.append({
                "fingerprint": f"manual:{move.id}",
                "name": _("Manual entry %(entry)s moves %(amount)s on %(accounts)s",
                          entry=move.display_name, amount=self._money(company, amount),
                          accounts=", ".join(sorted(set(group.account_id.mapped("display_name"))))),
                "severity": "high" if equity else "medium",
                "amount": amount,
                "date": move.date,
                "moves": move,
                "details": _("Entered by %(user)s in %(journal)s; above %(threshold)s, the usual ceiling for "
                             "manual entries on these accounts.", user=move.create_uid.name,
                             journal=move.journal_id.name, threshold=self._money(company, threshold)),
            })
        return results

    def _detect_backdated(self, company):
        """Manual entries dated well before the day they were created."""
        Move = self.env["account.move"]
        gap = int(self._param("max_gap"))
        moves = Move.search([
            ("company_id", "=", company.id), ("state", "=", "posted"), ("move_type", "=", "entry"),
            ("journal_id.type", "=", "general"), ("statement_line_id", "=", False),
            ("origin_payment_id", "=", False), ("reversed_entry_id", "=", False),
            ("create_date", ">=", fields.Datetime.now() - timedelta(days=int(self._param("days")))),
        ])
        results = []
        for move in moves:
            days = (move.create_date.date() - move.date).days
            if days <= gap:
                continue
            results.append({
                "fingerprint": f"backdated:{move.id}",
                "name": _("%(entry)s was dated %(days)s days before it was entered", entry=move.display_name,
                          days=days),
                "severity": "medium" if days > 3 * gap else "low",
                "amount": move.amount_total_signed and abs(move.amount_total_signed) or sum(
                    move.line_ids.mapped("debit")),
                "date": move.date,
                "moves": move,
                "details": _("Dated %(date)s, entered on %(created)s by %(user)s.",
                             date=fields.Date.to_string(move.date),
                             created=fields.Date.to_string(move.create_date.date()), user=move.create_uid.name),
            })
        return results

    def _detect_missing_analytic(self, company):
        """Income and expense lines without analytic, in a company that
        normally tags them (otherwise the check stays silent)."""
        AML = self.env["account.move.line"]
        pl = Domain("company_id", "=", company.id) & Domain("parent_state", "=", "posted") \
            & Domain("account_id.account_type", "in", PL_TYPES) & Domain("display_type", "=", "product")
        without = Domain("analytic_distribution", "in", [False])
        before = pl & Domain("date", "<", self._since(self._param("days"))) & Domain("date", ">=", self._since(
            int(self._param("days")) + 90))
        total = AML.search_count(before)
        if not total or 1 - AML.search_count(before & without) / total < float(self._param("min_share")):
            return []
        lines = AML.search(pl & without & Domain("date", ">=", self._since(self._param("days"))))
        results = []
        for move, group in _group_by(lines, lambda line: line.move_id).items():
            results.append({
                "fingerprint": f"analytic:{move.id}",
                "name": _("%(entry)s has %(count)s line(s) without analytic", entry=move.display_name,
                          count=len(group)),
                "severity": "low",
                "amount": abs(sum(group.mapped("balance"))),
                "date": move.date,
                "moves": move,
                "details": _("Lines on %(accounts)s have no analytic distribution, while most income and "
                             "expense lines of the company do.",
                             accounts=", ".join(sorted(set(group.account_id.mapped("display_name"))))),
            })
        return results

    def _detect_stale_bank(self, company):
        """Bank lines still unreconciled after a while, per journal."""
        StLine = self.env["account.bank.statement.line"]
        cutoff = self._since(self._param("days"))
        lines = StLine.search([
            ("company_id", "=", company.id), ("is_reconciled", "=", False), ("date", "<", cutoff)])
        results = []
        for journal, group in _group_by(lines, lambda line: line.journal_id).items():
            oldest = min(group.mapped("date"))
            suspense = journal.suspense_account_id
            balance = sum(self.env["account.move.line"].search([
                ("account_id", "=", suspense.id), ("parent_state", "=", "posted"),
                ("company_id", "=", company.id)]).mapped("balance")) if suspense else 0.0
            results.append({
                "fingerprint": f"stale_bank:{journal.id}",
                "name": _("%(count)s %(journal)s line(s) unreconciled for over %(days)s days",
                          count=len(group), journal=journal.name, days=self._param("days")),
                "severity": "high" if oldest < self._since(self._param("high_days")) else "medium",
                "amount": sum(abs(amount) for amount in group.mapped("amount")),
                "date": oldest,
                "moves": group.move_id[:50],
                "details": _("Oldest from %(date)s. The suspense account %(account)s holds %(balance)s.",
                             date=fields.Date.to_string(oldest), account=suspense.display_name or "-",
                             balance=self._money(company, balance)),
            })
        return results


class OdinAiFinding(models.Model):
    _name = "odin.ai.finding"
    _description = "Anomaly finding"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority, date desc, id desc"

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    detector_id = fields.Many2one("odin.ai.detector", required=True, ondelete="cascade", index=True)
    kind = fields.Selection(related="detector_id.kind")
    fingerprint = fields.Char(required=True)
    # group_expand: the kanban shows High, Medium, Low in that order, even when empty.
    severity = fields.Selection(SEVERITY, required=True, tracking=True, group_expand=True)
    priority = fields.Integer(compute="_compute_priority", store=True)
    state = fields.Selection([
        ("open", "Open"),
        ("in_progress", "In progress"),
        ("resolved", "Resolved"),
        ("auto_resolved", "Resolved by the check"),
        ("dismissed", "Dismissed"),
    ], default="open", required=True, tracking=True, index=True)
    user_id = fields.Many2one("res.users", string="Owner", tracking=True)
    date_deadline = fields.Date(string="Deadline", tracking=True)
    date = fields.Date()
    amount = fields.Monetary()
    move_ids = fields.Many2many("account.move", string="Entries")
    move_count = fields.Integer(compute="_compute_move_count")
    details = fields.Text()
    facts = fields.Json()
    last_seen = fields.Datetime(readonly=True)
    resolved_date = fields.Datetime(readonly=True)
    ai_summary = fields.Text(string="AI summary", readonly=True)
    ai_causes = fields.Text(string="Likely causes", readonly=True)
    ai_action = fields.Text(string="Suggested action", readonly=True)
    ai_date = fields.Datetime(readonly=True)

    _fingerprint_uniq = models.Constraint(
        "UNIQUE(company_id, detector_id, fingerprint)", "A check reports the same thing only once.")

    @api.depends("severity")
    def _compute_priority(self):
        for finding in self:
            finding.priority = PRIORITY.get(finding.severity, 3)

    @api.depends("move_ids")
    def _compute_move_count(self):
        for finding in self:
            finding.move_count = len(finding.move_ids)

    @api.model
    def action_run_checks(self):
        """The "Run checks now" button of the Anomalies screen."""
        return self.env["odin.ai.detector"].action_run_all()

    def action_start(self):
        self._check_accountant()
        self.filtered(lambda f: f.state == "open").write({"state": "in_progress", "user_id": self.env.uid})

    def action_resolve(self):
        self._check_accountant()
        self.write({"state": "resolved", "resolved_date": fields.Datetime.now()})

    def action_dismiss(self):
        self._check_accountant()
        self.write({"state": "dismissed", "resolved_date": fields.Datetime.now()})

    def action_reopen(self):
        self._check_accountant()
        self.write({"state": "open", "resolved_date": False})

    def action_open_entries(self):
        self.ensure_one()
        if len(self.move_ids) == 1:
            return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": self.move_ids.id,
                    "views": [(False, "form")], "target": "current"}
        return {
            "type": "ir.actions.act_window",
            "name": _("Entries"),
            "res_model": "account.move",
            "views": [(False, "list"), (False, "form")],
            "domain": [("id", "in", self.move_ids.ids)],
            "context": {"create": False},
        }

    def action_explain_ai(self):
        """Ask Claude for the likely cause and next step; kept on the finding
        and posted in its chatter."""
        self.ensure_one()
        if not self.env.user.has_group("odin_account_ai.group_ai_user") or \
                not self.env.user.has_group("account.group_account_readonly"):
            raise AccessError(_("Explain is for users who can see accounting."))
        self.check_access("read")
        handles = {}
        entries = []
        for move in self.move_ids[:5]:
            move.check_access("read")
            ref = f"E{len(handles) + 1}"
            handles[ref] = move.id
            entries.append({
                "ref": ref,
                "number": move.name,
                "type": move.move_type,
                "state": move.state,
                "date": fields.Date.to_string(move.date),
                "invoice_date": fields.Date.to_string(move.invoice_date) if move.invoice_date else None,
                "partner": (move.partner_id.display_name or "")[:100],
                "reference": (move.ref or "")[:100],
                "created": fields.Datetime.to_string(move.create_date),
                "created_by": move.create_uid.name,
                "lines": [
                    {"account": line.account_id.display_name, "label": (line.name or "")[:120],
                     "debit": round(line.debit, 2), "credit": round(line.credit, 2)}
                    for line in move.line_ids[:20]
                    if line.display_type not in ("line_section", "line_subsection", "line_note")
                ],
            })
        facts = {
            "check": self.detector_id.name,
            "check_description": self.detector_id.description or "",
            "finding": self.name,
            "why_it_fired": self.details or "",
            "figures": self.facts or {},
            "amount": self.amount,
            "entries": entries,
        }
        company = self.company_id
        payload = dumps(facts)
        data = self.env["odin.ai.llm"].call(
            "anomaly",
            system=prompts.ANOMALY_RULES.format(company=company.name, currency=company.currency_id.name),
            messages=[{"role": "user", "content": "The finding (data): " + payload}],
            schema=prompts.ANOMALY_SCHEMA,
            step_key="anomaly-%s-%s" % (self.id, hashlib.sha1(payload.encode()).hexdigest()),
        )
        answer = final_json(data)
        values = {
            "ai_summary": (answer.get("summary") or "").strip(),
            "ai_causes": "\n".join(f"• {cause.strip()}" for cause in answer.get("likely_causes") or []),
            "ai_action": (answer.get("suggested_action") or "").strip(),
            "ai_date": fields.Datetime.now(),
        }
        # Readers may not write findings; the explanation is theirs to keep.
        self.sudo().write(values)
        self.sudo().message_post(body=_(
            "AI explanation requested by %(user)s: %(summary)s Suggested: %(action)s",
            user=self.env.user.name, summary=values["ai_summary"], action=values["ai_action"]))
        return True

    @api.model
    def _check_accountant(self):
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only accountants can change findings."))


def _group_by(records, key):
    groups = {}
    for record in records:
        k = key(record)
        groups[k] = groups.get(k, record.browse()) | record
    return groups
