"""Draft Entry: a journal entry from a sentence, checked before it exists.

The accountant describes the entry ("accrue 120,000 audit fees for
September, reverse on 1 October"); Claude proposes the lines from the chart
of accounts it is given; the accountant edits them in the form. Partner and
analytic names the model returns are matched here, never trusted as ids, and
a name that matches nothing is reported instead of guessed.

Nothing reaches the books until the accountant presses a button, and then
only through the ORM, so Odoo's own checks apply on top of the ones shown
here (balance, active accounts of the company, lock dates of both dates,
a possible duplicate). An entry that should reverse is posted with its
reversal created as a draft that Odoo's own "Post draft entries with auto
post enabled" scheduled action posts on its date (``auto_post='at_date'``,
addons/account/models/account_move.py ``_autopost_draft_entries``).
"""

import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command

from . import odin_ai_prompts as prompts
from .odin_ai_llm import final_json
from .odin_ai_tools import dumps

MAX_ACCOUNTS_IN_PROMPT = 600
DUPLICATE_DAYS = 31


class OdinAiDraft(models.Model):
    _name = "odin.ai.draft"
    _description = "AI draft journal entry"
    _inherit = ["mail.thread"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(string="Reference", default=lambda self: _("New draft entry"), tracking=True)
    prompt = fields.Text(string="Describe the entry")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    journal_id = fields.Many2one(
        "account.journal", required=True, default=lambda self: self._default_journal(),
        domain="[('type', '=', 'general'), ('company_id', 'in', [company_id])]", check_company=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    reversal_date = fields.Date(help="If set, the entry is reversed on that date (for accruals).")
    line_ids = fields.One2many("odin.ai.draft.line", "draft_id", copy=True)
    notes = fields.Text(string="Assumptions", readonly=True)
    state = fields.Selection([("draft", "Draft"), ("created", "Entry created"), ("posted", "Posted")],
                             default="draft", required=True, tracking=True)
    move_id = fields.Many2one("account.move", readonly=True, copy=False)
    reversal_move_id = fields.Many2one("account.move", readonly=True, copy=False)
    total_debit = fields.Monetary(compute="_compute_totals")
    total_credit = fields.Monetary(compute="_compute_totals")
    issues = fields.Text(compute="_compute_issues", help="Blocking problems, one per line.")
    warnings = fields.Text(compute="_compute_issues", help="Things to check, one per line.")
    ai_ready = fields.Char(compute="_compute_ai_ready")

    @api.model
    def _default_journal(self):
        return self.env["account.journal"].search([
            *self.env["account.journal"]._check_company_domain(self.env.company), ("type", "=", "general")],
            limit=1)

    @api.depends("line_ids.debit", "line_ids.credit")
    def _compute_totals(self):
        for draft in self:
            draft.total_debit = sum(draft.line_ids.mapped("debit"))
            draft.total_credit = sum(draft.line_ids.mapped("credit"))

    def _compute_ai_ready(self):
        blocker = self.env["odin.ai.llm"].readiness() or False
        for draft in self:
            draft.ai_ready = blocker

    @api.depends("line_ids.debit", "line_ids.credit", "line_ids.account_id", "journal_id", "date",
                 "reversal_date", "company_id", "name")
    def _compute_issues(self):
        for draft in self:
            issues, warnings = draft._check()
            draft.issues = "\n".join(issues) or False
            draft.warnings = "\n".join(warnings) or False

    def _check(self):
        """(blocking issues, warnings) for the entry as it stands."""
        self.ensure_one()
        issues, warnings = [], []
        company = self.company_id
        currency = company.currency_id
        lines = self.line_ids
        if len(lines) < 2:
            issues.append(_("An entry needs at least two lines."))
        for line in lines:
            label = line.name or line.account_id.display_name or _("A line")
            if not line.account_id:
                issues.append(_("%(line)s has no account.", line=label))
            elif not line.account_id.active:
                issues.append(_("%(account)s is archived.", account=line.account_id.display_name))
            elif company not in line.account_id.company_ids:
                issues.append(_("%(account)s does not belong to %(company)s.",
                                account=line.account_id.display_name, company=company.name))
            if line.debit < 0 or line.credit < 0:
                issues.append(_("%(line)s has a negative amount.", line=label))
            elif bool(line.debit) == bool(line.credit):
                issues.append(_("%(line)s needs a debit or a credit, not both.", line=label))
            if line.partner_name and not line.partner_id:
                warnings.append(_("No partner found for “%(name)s”.", name=line.partner_name))
        if lines and not currency.is_zero(self.total_debit - self.total_credit):
            issues.append(_("The entry is not balanced: debit %(debit)s, credit %(credit)s.",
                            debit=self.total_debit, credit=self.total_credit))
        if self.journal_id and (self.journal_id.type != "general" or self.journal_id.company_id != company):
            issues.append(_("Use a miscellaneous journal of %(company)s.", company=company.name))
        for label, day in ((_("The date"), self.date), (_("The reversal date"), self.reversal_date)):
            if day and self.journal_id:
                locks = company._get_violated_lock_dates(day, False, self.journal_id)
                if locks:
                    issues.append(_("%(what)s is in a locked period (%(locks)s).", what=label,
                                    locks=company._format_lock_dates(locks)))
        if self.reversal_date and self.date and self.reversal_date <= self.date:
            issues.append(_("The reversal date must be after the entry's date."))
        if self.date and lines and self.total_debit and self.journal_id:
            similar = self.env["account.move"].search_count([
                ("company_id", "=", company.id), ("journal_id", "=", self.journal_id.id),
                ("state", "in", ("draft", "posted")), ("id", "!=", self.move_id.id),
                # amount_total of a miscellaneous entry is its total debit, signed.
                ("amount_total", "in", [currency.round(self.total_debit), -currency.round(self.total_debit)]),
                ("date", ">=", fields.Date.subtract(self.date, days=DUPLICATE_DAYS)),
                ("date", "<=", fields.Date.add(self.date, days=DUPLICATE_DAYS)),
            ])
            if similar:
                warnings.append(_("%(count)s entry(ies) of the same amount in this journal within a month: "
                                  "check it is not already booked.", count=similar))
        return issues, warnings

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_generate(self):
        """Ask Claude for the lines; they replace the current ones."""
        self.ensure_one()
        self._check_accountant()
        if self.state != "draft":
            raise UserError(_("The entry is already created."))
        description = (self.prompt or "").strip()
        if not description:
            raise UserError(_("Describe the entry first."))
        company = self.company_id
        accounts = self.env["account.account"].search(
            [("company_ids", "in", company.ids)], limit=MAX_ACCOUNTS_IN_PROMPT)
        by_code = {account.code: account for account in accounts if account.code}
        system = prompts.DRAFT_RULES.format(
            company=company.name, currency=company.currency_id.name,
            accounts="\n".join(f"  {code} {account.name} [{account.account_type}]"
                               for code, account in sorted(by_code.items())))
        content = "\n".join([
            "Today is %s." % fields.Date.to_string(fields.Date.context_today(self)),
            "Journal: %s." % self.journal_id.name,
            "Description (data): %s" % dumps(description[:3000]),
        ])
        data = self.env["odin.ai.llm"].call(
            "draft", system=system, schema=prompts.DRAFT_SCHEMA,
            messages=[{"role": "user", "content": content}],
            step_key="draft-%s-%s" % (self.id, hashlib.sha1(content.encode()).hexdigest()),
        )
        answer = final_json(data)
        notes = [note for note in answer.get("notes") or [] if isinstance(note, str)]
        commands = [Command.clear()]
        for line in answer.get("lines") or []:
            code = (line.get("account_code") or "").strip()
            account = by_code.get(code)
            if not account:
                notes.append(_("The AI proposed account %(code)s, which is not in the chart; pick one.", code=code))
            partner_name = (line.get("partner") or "").strip()
            partner = self._find_partner(partner_name) if partner_name else self.env["res.partner"]
            analytic_name = (line.get("analytic_account") or "").strip()
            analytic = self.env["account.analytic.account"].search(
                [("name", "=ilike", analytic_name), ("company_id", "in", [False, company.id])],
                limit=1) if analytic_name else False
            if analytic_name and not analytic:
                notes.append(_("No analytic account named “%(name)s”.", name=analytic_name))
            commands.append(Command.create({
                "account_id": account.id if account else False,
                "name": (line.get("label") or "")[:200],
                "debit": company.currency_id.round(max(float(line.get("debit") or 0.0), 0.0)),
                "credit": company.currency_id.round(max(float(line.get("credit") or 0.0), 0.0)),
                "partner_id": partner.id if partner else False,
                "partner_name": partner_name or False,
                "analytic_distribution": {str(analytic.id): 100.0} if analytic else False,
            }))
        values = {"line_ids": commands}
        if answer.get("ref"):
            values["name"] = answer["ref"][:120]
        for field in ("date", "reversal_date"):
            day = answer.get(field)
            if day:
                try:
                    values[field] = fields.Date.to_date(day)
                except ValueError:
                    notes.append(_("Could not read the date %(date)s.", date=day))
            elif field == "reversal_date":
                values[field] = False
        values["notes"] = "\n".join(f"• {note}" for note in notes) or False
        self.write(values)
        return True

    def action_create_entry(self):
        """The draft journal entry, to review and post in Odoo as usual."""
        self.ensure_one()
        move = self._create_move()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": move.id,
                "views": [(False, "form")], "target": "current"}

    def action_post(self):
        """Post the entry and, with a reversal date, schedule its reversal."""
        self.ensure_one()
        move = self.move_id if self.state == "created" else self._create_move()
        if move.state == "draft":
            move.action_post()
        if self.reversal_date and not self.reversal_move_id:
            reversal = move._reverse_moves([{
                "date": self.reversal_date,
                "ref": _("Reversal of: %(entry)s", entry=move.name),
                # auto_post is not copied on reversal: set it explicitly.
                "auto_post": "at_date",
            }])
            self.reversal_move_id = reversal
        self.state = "posted"
        self.message_post(body=_("Posted %(entry)s%(reversal)s.", entry=move._get_html_link(), reversal=_(
            ", reversal %(rev)s scheduled on %(date)s", rev=self.reversal_move_id._get_html_link(),
            date=fields.Date.to_string(self.reversal_date)) if self.reversal_move_id else ""))
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": move.id,
                "views": [(False, "form")], "target": "current"}

    def action_open_move(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": self.move_id.id,
                "views": [(False, "form")], "target": "current"}

    def _create_move(self):
        self._check_accountant()
        if self.state != "draft":
            raise UserError(_("The entry is already created."))
        issues, _warnings = self._check()
        if issues:
            raise UserError("\n".join(issues))
        move = self.env["account.move"].create({
            "move_type": "entry",
            "company_id": self.company_id.id,
            "journal_id": self.journal_id.id,
            "date": self.date,
            "ref": self.name,
            "line_ids": [Command.create({
                "account_id": line.account_id.id,
                "name": line.name,
                "debit": line.debit,
                "credit": line.credit,
                "partner_id": line.partner_id.id,
                "analytic_distribution": line.analytic_distribution,
            }) for line in self.line_ids],
        })
        move.message_post(body=_("Drafted with Accounting AI by %(user)s from: “%(prompt)s”",
                                 user=self.env.user.name, prompt=(self.prompt or "")[:500]))
        self.write({"move_id": move.id, "state": "created"})
        return move

    def _find_partner(self, name):
        Partner = self.env["res.partner"]
        company_domain = [("company_id", "in", [False, self.company_id.id])]
        partner = Partner.search([("name", "=ilike", name), *company_domain], limit=1)
        if not partner:
            matches = Partner.search([("name", "ilike", name), *company_domain], limit=2)
            partner = matches if len(matches) == 1 else Partner
        return partner

    @api.model
    def _check_accountant(self):
        # account.move._post only needs Invoicing rights; journal entries are
        # an accountant's job, so it is checked here.
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only accountants can draft journal entries."))


class OdinAiDraftLine(models.Model):
    _name = "odin.ai.draft.line"
    _description = "AI draft journal entry line"
    _inherit = ["analytic.mixin"]
    _order = "sequence, id"

    draft_id = fields.Many2one("odin.ai.draft", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="draft_id.company_id", store=True)
    currency_id = fields.Many2one(related="draft_id.currency_id")
    sequence = fields.Integer(default=10)
    account_id = fields.Many2one("account.account", domain="[('company_ids', 'in', [company_id])]")
    name = fields.Char(string="Label")
    partner_id = fields.Many2one("res.partner")
    partner_name = fields.Char(help="The name the AI gave, kept when it matched no partner.")
    debit = fields.Monetary()
    credit = fields.Monetary()
