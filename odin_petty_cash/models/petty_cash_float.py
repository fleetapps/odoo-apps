from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


class PettyCashFloat(models.Model):
    """One float: the M-Pesa line (or cash box) the team pays from. Its money
    sits in the journal's default account; every approved voucher posts there."""

    _name = "odin.petty.cash.float"
    _description = "Petty cash float"
    _order = "sequence, id"

    name = fields.Char(required=True, default=lambda self: _("M-Pesa float"))
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    phone = fields.Char(string="M-Pesa number")
    journal_id = fields.Many2one(
        "account.journal", required=True, check_company=True,
        domain="[('type', 'in', ('cash', 'bank'))]",
        help="Cash journal the vouchers post to. Its default account holds the float.")
    account_id = fields.Many2one(related="journal_id.default_account_id", string="Float account")
    charge_account_id = fields.Many2one(
        "account.account", string="M-Pesa charges account", check_company=True,
        help="Transaction costs post here, e.g. Bank Charges.")
    charge_partner_id = fields.Many2one("res.partner", string="Charges paid to",
                                        help="Usually Safaricom.")
    topup_account_id = fields.Many2one(
        "account.account", string="Top-ups come from", check_company=True,
        help="Account credited when the float is funded, unless a rule says otherwise "
        "(e.g. Director's current account).")
    stock_rule_ids = fields.Many2many(
        "account.reconcile.model", string="Rules that buy stock",
        help="When one of these rules matches, the voucher asks for the product and quantity "
        "and receives them into stock, so the purchase counts in cost of goods.")
    approver_ids = fields.Many2many("res.users", string="Approvers",
                                    help="Who may approve. Empty: any Petty Cash approver.")
    voucher_prefix = fields.Char(default="PCV", required=True)
    statement_balance = fields.Monetary(readonly=True, help="Balance on the last M-Pesa statement uploaded.")
    statement_date = fields.Datetime(readonly=True)
    line_ids = fields.One2many("odin.petty.cash.line", "float_id")

    # ------------------------------------------------------------------ book
    def _book_balance(self, before=None, include_draft=False):
        """Float account balance from posted entries (before a date if given),
        plus unposted vouchers when asked."""
        self.ensure_one()
        domain = [("account_id", "=", self.account_id.id), ("parent_state", "=", "posted"),
                  ("company_id", "=", self.company_id.id)]
        if before:
            domain.append(("date", "<", before))
        groups = self.env["account.move.line"]._read_group(domain, [], ["balance:sum"])
        balance = groups[0][0] if groups else 0.0
        if include_draft:
            drafts = self.line_ids.filtered(lambda l: l.state == "draft" and (not before or l.date < before))
            balance += sum(drafts.mapped("signed_total"))
        return balance or 0.0

    def _can_approve(self):
        self.ensure_one()
        user = self.env.user
        if not user.has_group("odin_petty_cash.group_petty_cash_approver"):
            return False
        return not self.approver_ids or user in self.approver_ids

    @api.model
    def get_book(self, float_id=False, year=False, month=False, status=False, search=""):
        """Everything the Petty Cash Book page shows for one month."""
        floats = self.search([])
        if not floats:
            return {"floats": [], "empty": True,
                    "can_configure": self.env.user.has_group("odin_petty_cash.group_petty_cash_approver")}
        flt = floats.filtered(lambda f: f.id == float_id)[:1] or floats[:1]
        today = fields.Date.context_today(self)
        year = year or today.year
        month = month or today.month
        start = date(year, month, 1)
        end = start + relativedelta(months=1)
        Line = self.env["odin.petty.cash.line"]
        month_lines = Line.search([("float_id", "=", flt.id), ("date", ">=", start), ("date", "<", end)],
                                  order="date, voucher_seq, id")
        opening = flt._book_balance(before=start, include_draft=True)

        running = opening
        rows = []
        counts = {"need": 0, "ready": 0, "stock": 0, "posted": 0}
        spent = received = 0.0
        for line in month_lines:
            running += line.signed_total
            key = line.status_key
            if key in ("need_description", "need_category", "need_receipt", "need_amount"):
                counts["need"] += 1
            elif key == "need_stock":
                counts["stock"] += 1
            elif key == "ready":
                counts["ready"] += 1
            elif key == "posted":
                counts["posted"] += 1
            if line.kind == "topup":
                received += line.amount
            else:
                spent += line.total
            row = line._book_row()
            row["balance"] = float_round(running, 2)
            rows.append(row)
        if status:
            groups = {"need": ("need_description", "need_category", "need_receipt", "need_amount"),
                      "stock": ("need_stock",), "ready": ("ready",), "posted": ("posted",)}
            rows = [r for r in rows if r["status_key"] in groups.get(status, ())]
        if search:
            needle = search.lower()
            rows = [r for r in rows if needle in " ".join(
                str(r[k] or "") for k in ("voucher", "description", "paid_to", "phone", "mpesa_code", "category")
            ).lower()]

        per_month = dict.fromkeys(range(1, 13), 0)
        for group_month, count in Line._read_group(
                [("float_id", "=", flt.id), ("date", ">=", date(year, 1, 1)), ("date", "<", date(year + 1, 1, 1))],
                ["date:month"], ["__count"]):
            per_month[group_month.month] = count
        older = Line.search([("float_id", "=", flt.id), ("state", "=", "draft"), ("date", "<", start)],
                            order="date")
        rules = self.env["account.reconcile.model"].search(flt._rule_domain())
        book_balance = flt._book_balance()
        projected = flt._book_balance(include_draft=True)
        return {
            "floats": [{"id": f.id, "name": f.name} for f in floats],
            "float": {
                "id": flt.id, "name": flt.name, "phone": flt.phone or "",
                "currency": flt.currency_id.symbol, "book_balance": book_balance,
                "projected_balance": projected,
                "statement_balance": flt.statement_balance if flt.statement_date else None,
                "statement_date": fields.Datetime.to_string(flt.statement_date) if flt.statement_date else False,
            },
            "year": year, "month": month,
            "months": [{"month": i, "label": MONTHS[i - 1], "count": per_month[i]} for i in range(1, 13)],
            "opening": opening, "closing": running,
            "spent": spent, "received": received,
            "counts": counts,
            "older_open": {"count": len(older), "year": older[:1].date.year if older else False,
                           "month": older[:1].date.month if older else False},
            "rows": rows,
            "rules": [{"id": r.id, "name": r.name.removeprefix("Auto: ")} for r in rules],
            "can_approve": flt._can_approve(),
            "today": fields.Date.to_string(today),
        }

    def _rule_domain(self):
        self.ensure_one()
        return [("company_id", "=", self.company_id.id), ("match_label", "!=", False),
                "|", ("match_journal_ids", "=", False), ("match_journal_ids", "in", self.journal_id.ids)]

    def action_open_book(self):
        return {"type": "ir.actions.client", "tag": "odin_petty_cash.book",
                "params": {"float_id": self.id}, "name": self.name}

    def action_upload_statement(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "res_model": "odin.petty.cash.import",
            "view_mode": "form", "target": "new", "name": _("Upload M-Pesa statement"),
            "context": {"default_float_id": self.id},
        }

    @api.model
    def action_add_float(self, float_id, amount, note=False, code=False, when=False):
        """Fund the float: a top-up line, posted straight away by an approver."""
        flt = self.browse(float_id)
        if amount <= 0:
            raise UserError(_("Enter the amount sent to the float."))
        line = self.env["odin.petty.cash.line"].create({
            "float_id": flt.id, "kind": "topup", "amount": amount, "method": "mpesa",
            "description": note or _("FLOAT TOP UP"), "paid_to": self.env.user.name,
            "mpesa_code": code or False, "date": when or fields.Date.context_today(self),
        })
        if flt._can_approve() and line.status_key == "ready":
            line.action_approve()
        return line.id
