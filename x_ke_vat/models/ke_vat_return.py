# -*- coding: utf-8 -*-
"""A Kenyan VAT return: one period, frozen, locked, and reconcilable.

A rendered report is a view of a moving ledger. A filed return is a promise
about a period that has stopped moving. This model is the difference between
them: it takes the renderer's output, freezes it, writes the credit carried
forward so next month picks it up, and sets the tax lock date. From then on a
new entry dated inside the period is not refused: Odoo moves its accounting date
to the first open day after the lock, so it reports in the next return rather
than changing this one. Editing the tax figures of an entry already inside the
period is refused.

Three things here are deliberate.

Closing is idempotent. Re-closing a period must not carry the same credit
forward twice, so every external value a return writes is stamped with that
return and replaced wholesale rather than appended to. A double-counted credit
is invisible until KRA disagrees with you months later.

Reopening does not unlock. It is tempting to have ``action_reopen`` lower the
tax lock date, and it is wrong: the period may already have been filed, and
silently making a filed period writable again is the failure this model exists
to prevent. Reopening reverts the state and says, in as many words, that the
lock is still in place and where to lift it.

Posting is opt-in. Closing freezes and locks whether or not anything is posted
to the ledger, because the first thing anyone should do with this module is run
it in parallel against a period that has already been filed, and that has to be
possible without writing a single journal entry.
"""

import base64
import csv
import io
import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain
from odoo.tools.misc import formatLang

_logger = logging.getLogger(__name__)

# Boxes the VAT entry is built from. Boxes 19 to 21 (credit brought forward,
# withholding credit, refund claim) are settlement positions against other
# accounts, and posting them belongs with the withholding-VAT certificate
# register rather than here. The entry posts the period's own VAT movement:
#
#   debit  output VAT account        box 6    clears the output VAT credited by sales
#   credit input VAT account         box 12   clears the input VAT debited by purchases
#   credit import VAT account        box 13   VAT on imported services, wherever it was posted
#   debit  non-deductible account    box 14 + box 16   input VAT the return does not allow
#   payable or credit account        the balance, which is box 18 by construction
#
# box 18 = box 6 - box 17 and box 17 = box 12 + box 13 - box 14 - box 16, so
# the balancing line equals box 18 exactly; it is nonetheless computed as the
# difference of the rounded lines rather than rounded on its own, because
# round(a) - round(b) and round(a - b) can differ by a cent and Odoo refuses an
# entry that is a cent out.
BOX_OUTPUT_VAT = ("box_6", "tax")
BOX_INPUT_VAT = ("box_12", "tax")
BOX_IMPORT_VAT = ("box_13", "tax")
BOX_EXEMPT_ONLY_INPUT_VAT = ("box_14", "tax")
BOX_NON_DEDUCTIBLE_INPUT_VAT = ("box_16", "tax")


class KeVatReturn(models.Model):
    _name = "ke.vat.return"
    _description = "Kenyan VAT Return"
    _order = "date_from desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, compute="_compute_name", store=True,
                       readonly=False, precompute=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    report_id = fields.Many2one(
        "account.report", string="Return Definition", required=True,
        default=lambda self: self.env.ref(
            "l10n_ke.tax_report_ke", raise_if_not_found=False),
        help="The account.report this return renders. Defaults to the VAT3 "
             "definition shipped by l10n_ke.")
    date_from = fields.Date(
        required=True, default=lambda self: self._default_date_from())
    date_to = fields.Date(
        required=True, default=lambda self: self._default_date_to())
    state = fields.Selection(
        [("draft", "Draft"), ("closed", "Closed")],
        default="draft", required=True, index=True, copy=False)

    period_name = fields.Char(
        compute="_compute_period_name", store=True,
        help="What the deployment calls this period. Taken from the seeded "
             "statutory periods where they exist, so that a return and a "
             "report filter name the same month the same way.")

    # Nothing below is copied: a duplicated return would otherwise clone the
    # typed values into the same period and sum them twice.
    line_ids = fields.One2many(
        "ke.vat.return.line", "return_id", readonly=True, copy=False)
    manual_value_ids = fields.One2many(
        "account.report.external.value", "ke_vat_return_id",
        string="Manual Entries", copy=False,
        domain=[("carryover_origin_expression_label", "=", False)])
    batch_ids = fields.One2many(
        "ke.vat.itax.batch", "return_id", string="iTax Imports", copy=False)
    batch_count = fields.Integer(compute="_compute_batch_count")

    move_id = fields.Many2one(
        "account.move", string="VAT Entry", readonly=True, copy=False,
        check_company=True)
    itax_ack_ref = fields.Char(
        string="iTax Acknowledgement", copy=False,
        help="The acknowledgement number iTax returns on filing. Recorded here "
             "so the period, the figures and the filing reference live "
             "together.")
    itax_filed_date = fields.Date(string="Filed On", copy=False)

    _period_uniq = models.Constraint(
        "UNIQUE(company_id, report_id, date_from, date_to)",
        "A return already exists for that company, definition and period.")

    # ------------------------------------------------------------ defaults

    @api.model
    def _default_date_from(self):
        """The month before this one: the period you are about to file."""
        today = fields.Date.context_today(self)
        return (today - relativedelta(months=1)).replace(day=1)

    @api.model
    def _default_date_to(self):
        first_of_this_month = fields.Date.context_today(self).replace(day=1)
        return first_of_this_month - relativedelta(days=1)

    @api.depends("report_id", "date_from", "date_to", "company_id")
    def _compute_name(self):
        for record in self:
            if record.report_id and record.date_from and record.date_to:
                record.name = "%s %s" % (
                    record.report_id.name,
                    record.company_id._ke_period_name(
                        record.date_from, record.date_to))
            else:
                record.name = record.name or _("New VAT Return")

    @api.depends("company_id", "date_from", "date_to")
    def _compute_period_name(self):
        for record in self:
            if record.company_id and record.date_from and record.date_to:
                record.period_name = record.company_id._ke_period_name(
                    record.date_from, record.date_to)
            else:
                record.period_name = False

    @api.depends("batch_ids")
    def _compute_batch_count(self):
        for record in self:
            record.batch_count = len(record.batch_ids)

    @api.constrains("date_from", "date_to")
    def _check_period(self):
        for record in self:
            if record.date_from and record.date_to and record.date_from > record.date_to:
                raise ValidationError(_(
                    "%(name)s starts on %(start)s and ends on %(end)s, which "
                    "is before it starts.",
                    name=record.display_name, start=record.date_from,
                    end=record.date_to))

    # ---------------------------------------------------------- integrity

    # Fields only the close/reopen actions may change. A user with write
    # access could otherwise set state='closed' over RPC without freezing the
    # lines, writing the carryover or setting the lock.
    _ACTION_ONLY_FIELDS = ("state", "move_id")

    def write(self, vals):
        if not self.env.context.get("ke_vat_action") and any(
                field in vals for field in self._ACTION_ONLY_FIELDS):
            raise UserError(_(
                "The status of a VAT return changes only through Close Period "
                "and Reopen."))
        return super().write(vals)

    def _as_action(self):
        """The environment that may change state: the two actions only."""
        return self.with_context(ke_vat_action=True)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        for record in self:
            if record.state != "draft":
                raise UserError(_(
                    "%(name)s is closed. Reopen it before deleting it, so "
                    "that the credit it carried forward is withdrawn too.",
                    name=record.display_name))

    def unlink(self):
        """Take the return's manual boxes with it.

        The six typed values are account.report.external.value rows that only
        this return knows about. Left behind, they are still summed into the
        next return created for the same period, invisibly, because the
        ``sum`` engine is keyed on the expression and the date and not on the
        return. Carryover rows never exist on a draft return: reopening
        withdraws them, and only a draft can be deleted.
        """
        self._unlink_only_draft()
        self.env["account.report.external.value"].search([
            ("ke_vat_return_id", "in", self.ids),
        ]).unlink()
        return super().unlink()

    # ------------------------------------------------------------- options

    def _render_options(self):
        """The options dict this return renders under."""
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_id.ids,
            "state": "posted",
            "journal_ids": [],
        }

    # ------------------------------------------------------------- compute

    def action_compute(self):
        """Re-render the return and refresh its frozen lines."""
        for record in self:
            if record.state != "draft":
                raise UserError(_(
                    "%(name)s is closed. Reopen it before recomputing.",
                    name=record.display_name))
            rendered = self.env["ke.vat.render"].render(
                record.report_id, record._render_options())
            record._ensure_manual_entries()
            record._store_lines(rendered)
        return True

    def _store_lines(self, rendered):
        """Replace the snapshot with what the renderer just produced."""
        self.ensure_one()
        self.line_ids.unlink()
        labels = [column["expression_label"] for column in rendered["columns"]]
        base_label = labels[0] if labels else None
        tax_label = labels[1] if len(labels) > 1 else None
        self.env["ke.vat.return.line"].create([
            {
                "return_id": self.id,
                "report_line_id": line["id"],
                "code": line["code"],
                "name": line["name"],
                "level": line["level"],
                "sequence": line["sequence"],
                "base_value": line["values"].get(base_label) or 0.0,
                "tax_value": line["values"].get(tax_label) or 0.0,
                "has_base": line["values"].get(base_label) is not None,
                "has_tax": line["values"].get(tax_label) is not None,
            }
            for line in rendered["lines"]
        ])

    def _ensure_manual_entries(self):
        """Give every editable box a real record to type into.

        The six editable boxes are stored as ``account.report.external.value``
        rows. Creating them up front, at zero, means the manual-entry page is a
        plain editable list of the real records rather than a set of shadow
        fields that have to be kept in step with the thing they shadow.
        """
        self.ensure_one()
        render = self.env["ke.vat.render"]
        external_value = self.env["account.report.external.value"]

        expressions = self.report_id.line_ids.expression_ids.filtered(
            render.is_editable)
        missing = expressions - self.manual_value_ids.mapped(
            "target_report_expression_id")
        if missing:
            external_value.create([
                {
                    "name": expression.report_line_id.name,
                    "value": 0.0,
                    "date": self.date_to,
                    "target_report_expression_id": expression.id,
                    "company_id": self.company_id.id,
                    "ke_vat_return_id": self.id,
                }
                for expression in missing
            ])

    # --------------------------------------------------------------- close

    def _check_manager(self, action):
        """``groups=`` on a button hides it. It does not enforce anything.

        Closing sets the company's tax lock date and reopening steps back from
        a declared period, so both are gated here as well as in the view.
        """
        if not self.env.user.has_group("x_ke_base.group_ke_tax_manager"):
            raise AccessError(_(
                "Only a Kenya VAT Manager can %(action)s a return, because it "
                "changes the tax lock date for the whole company.",
                action=action))

    def action_close(self):
        self._check_manager(_("close"))
        for record in self:
            if record.state != "draft":
                raise UserError(_(
                    "%(name)s is already closed.", name=record.display_name))
            record._check_previous_period_closed()

            rendered = self.env["ke.vat.render"].render(
                record.report_id, record._render_options())
            record._store_lines(rendered)
            record._write_carryover(rendered)

            if record.company_id.ke_vat_post_on_close:
                record._post_vat_entry(rendered)

            record._set_tax_lock_date()
            record._as_action().state = "closed"
        return True

    def _check_previous_period_closed(self):
        """Periods close in order, because the credit brought forward is read
        as "the most recent carryover on or before the end of the previous
        period". Skip a month and the month after it quietly reuses the last
        credit that was closed. A first return has nothing before it and is
        allowed; once any earlier return has been closed, the immediately
        previous period must be closed too.
        """
        self.ensure_one()
        Return = self.env["ke.vat.return"]
        earlier_closed = Return.search([
            ("company_id", "=", self.company_id.id),
            ("report_id", "=", self.report_id.id),
            ("state", "=", "closed"),
            ("date_to", "<", self.date_from),
        ], limit=1)
        if not earlier_closed:
            return
        previous_from, previous_to = self.company_id._ke_previous_period(
            self.date_from, self.date_to)
        previous = Return.search([
            ("company_id", "=", self.company_id.id),
            ("report_id", "=", self.report_id.id),
            ("date_from", "=", previous_from),
            ("date_to", "=", previous_to),
        ], limit=1)
        if previous.state == "closed":
            return
        period = self.company_id._ke_period_name(previous_from, previous_to)
        raise UserError(_(
            "%(what)s before closing %(name)s. Box 19 reads the credit "
            "carried forward from the most recently closed period, so closing "
            "out of order would bring forward the wrong credit.",
            what=_("Close the return for %(period)s", period=period) if previous
            else _("Create and close a return for %(period)s", period=period),
            name=self.display_name))

    def _write_carryover(self, rendered):
        """Carry the period's credit forward, exactly once.

        Box 26 computes ``_carryover_tax`` (itself, but only when the period
        nets to a credit) and box 19 reads it back next period through its
        ``_applied_carryover_tax`` expression: engine ``external``, formula
        ``most_recent``, date scope ``previous_return_period``. Two things
        follow from that definition, and both are load-bearing.

        The value is dated the last day of THIS period. Next period resolves
        ``previous_return_period`` to this period and takes the most recent
        value on or before its end, so a value dated inside this period is
        found and a value dated the day after is not -- it would be found one
        period too late, by the period after next. This period's own render
        never sees it either, because its previous-period window ends before
        this period starts.

        The value is written even when it is zero. ``most_recent`` has no
        memory of periods: if a payable month wrote nothing, the month after
        it would reach back and apply the last credit a second time. A zero
        record is what says "nothing was carried forward from here".

        Everything this return wrote before is removed first, so closing a
        second time replaces the credit rather than adding to it.
        """
        self.ensure_one()
        external_value = self.env["account.report.external.value"]
        options = rendered["options"]
        values = rendered["expression_values"]
        carry_date = self.date_to

        external_value.search([
            ("ke_vat_return_id", "=", self.id),
            ("carryover_origin_expression_label", "!=", False),
        ]).unlink()

        expressions = self.report_id.line_ids.expression_ids.filtered(
            lambda expression: expression.label.startswith("_carryover_"))
        for expression in expressions:
            amount = values.get(expression.id, 0.0)
            target = expression._get_carryover_target_expression(options)
            if not target:
                continue
            external_value.create({
                "name": _("Carried forward from %(period)s",
                          period=self.display_name),
                "value": amount,
                "date": carry_date,
                "target_report_expression_id": target.id,
                "company_id": self.company_id.id,
                "carryover_origin_expression_label": expression.label,
                "carryover_origin_report_line_id": expression.report_line_id.id,
                "ke_vat_return_id": self.id,
            })

    def _set_tax_lock_date(self):
        """Stop the period moving after it has been declared.

        Community gives this outright: the field is on res.company.
        account.move.line._check_tax_lock_date refuses a change to the tax
        figures of an entry already dated at or before it, and account.move._post
        moves a *new* entry dated inside the locked period to the first open
        day after it (the official wording: its tax values are "moved to the
        next open tax period"). Never lower it here; a company whose lock is
        already further forward is left alone.
        """
        self.ensure_one()
        company = self.company_id
        if company.tax_lock_date and company.tax_lock_date >= self.date_to:
            return
        company.sudo().write({"tax_lock_date": self.date_to})
        _logger.info(
            "x_ke_vat: tax lock date for %s set to %s on closing %s",
            company.display_name, self.date_to, self.display_name)

    def _post_vat_entry(self, rendered):
        """Post the period's own VAT movement, clearing the control accounts.

        Every line is stated as "this account, and the amount to debit it by"
        (a negative amount is a credit). The output and input control accounts
        are cleared by what the sales and purchases actually posted to them,
        boxes 6 and 12 - not by box 17, which is net of amounts that never
        touched the input VAT account. The non-deductible part of the input
        pool, boxes 14 and 16, is expensed. VAT on imported services (box 13)
        is credited from wherever l10n_ke posted it; that account defaults to
        the input VAT account.

        Boxes 19 to 21 are deliberately not posted. The credit brought forward,
        the withholding credit and the refund claim are settlement positions
        against accounts this module does not own.

        The entry is reused across reopen and re-close, so a return has one
        entry for its whole life instead of a trail of drafts.
        """
        self.ensure_one()
        company = self.company_id
        currency = company.currency_id

        output = self._box_value(rendered, *BOX_OUTPUT_VAT)
        input_vat = self._box_value(rendered, *BOX_INPUT_VAT)
        import_vat = self._box_value(rendered, *BOX_IMPORT_VAT)
        non_deductible = (self._box_value(rendered, *BOX_EXEMPT_ONLY_INPUT_VAT)
                          + self._box_value(rendered, *BOX_NON_DEDUCTIBLE_INPUT_VAT))
        company._ke_vat_check_posting_config(
            needs_import=not currency.is_zero(import_vat),
            needs_non_deductible=not currency.is_zero(non_deductible))

        entries = [
            (company.ke_vat_output_account_id, output,
             _("Output VAT for %(period)s", period=self.display_name)),
            (company.ke_vat_input_account_id, -input_vat,
             _("Input VAT for %(period)s", period=self.display_name)),
            (company.ke_vat_import_vat_account_id or company.ke_vat_input_account_id,
             -import_vat,
             _("VAT on imported services for %(period)s", period=self.display_name)),
            (company.ke_vat_non_deductible_account_id, non_deductible,
             _("Non-deductible input VAT for %(period)s", period=self.display_name)),
        ]

        lines = []
        balance = 0.0
        for account, amount, label in entries:
            amount = currency.round(amount)
            if currency.is_zero(amount):
                continue
            debit, credit = (amount, 0.0) if amount > 0 else (0.0, -amount)
            balance += debit - credit
            lines.append({
                "name": label,
                "account_id": account.id,
                "debit": debit,
                "credit": credit,
            })

        # The settlement line closes the entry from the rounded lines above,
        # so it is exact by construction. It is box 18: positive means VAT
        # payable, negative means a credit due to us.
        position = currency.round(balance)
        if not currency.is_zero(position):
            lines.append({
                "name": _("VAT payable for %(period)s", period=self.display_name)
                        if position > 0
                        else _("VAT credit for %(period)s", period=self.display_name),
                "account_id": (company.ke_vat_payable_account_id if position > 0
                               else company.ke_vat_credit_account_id).id,
                "debit": 0.0 if position > 0 else -position,
                "credit": position if position > 0 else 0.0,
            })
        if not lines:
            return

        values = {
            "move_type": "entry",
            "journal_id": company.ke_vat_journal_id.id,
            "company_id": company.id,
            "date": self.date_to,
            "ref": self.display_name,
            "line_ids": [(5, 0, 0)] + [(0, 0, line) for line in lines],
        }
        move = self.move_id
        if move and move.state == "draft":
            move.with_company(company).write(values)
        else:
            move = self.env["account.move"].with_company(company).create(values)
        move.action_post()
        self._as_action().move_id = move

    @api.model
    def _box_value(self, rendered, code, label):
        for line in rendered["lines"]:
            if line["code"] == code:
                return line["values"].get(label) or 0.0
        return 0.0

    # -------------------------------------------------------------- reopen

    def action_reopen(self):
        """Return to draft without touching the lock.

        Deliberate: the period may already have been filed. Lowering the tax
        lock date here would make a declared period quietly writable again,
        which is the exact failure closing exists to prevent. The lock is lifted
        by hand, in Settings, by someone who has decided to lift it.
        """
        self._check_manager(_("reopen"))
        for record in self:
            if record.state != "closed":
                raise UserError(_(
                    "%(name)s is not closed.", name=record.display_name))
            if record.itax_ack_ref:
                raise UserError(_(
                    "%(name)s was filed with iTax under %(ref)s. Clear the "
                    "acknowledgement first if you really mean to reopen it.",
                    name=record.display_name, ref=record.itax_ack_ref))

            self.env["account.report.external.value"].search([
                ("ke_vat_return_id", "=", record.id),
                ("carryover_origin_expression_label", "!=", False),
            ]).unlink()

            # The entry goes back to draft and stays attached: re-closing
            # rewrites and reposts it rather than leaving a draft behind and
            # creating a second one.
            if record.move_id and record.move_id.state == "posted":
                record.move_id.button_draft()
            record._as_action().state = "draft"
        return True

    # -------------------------------------------------------------- export

    def action_export_csv(self):
        """Download the frozen boxes as a CSV.

        A convenience rather than a filing route. Since March 2024 KRA
        pre-fills the return and the schedules are no longer keyed in, so this
        exists for working papers, for the audit file, and for anyone who wants
        the figures in a spreadsheet next to KRA's own.
        """
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_(
                "There is nothing to export yet. Compute the return first."))

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([_("Box"), _("Description"), _("Base"), _("VAT")])
        for line in self.line_ids:
            writer.writerow([
                line.code or "",
                line.name,
                line.base_value if line.has_base else "",
                line.tax_value if line.has_tax else "",
            ])

        attachment = self.env["ir.attachment"].create({
            "name": "%s.csv" % (self.display_name or "vat_return").replace("/", "-"),
            "type": "binary",
            "datas": base64.b64encode(buffer.getvalue().encode("utf-8")),
            "mimetype": "text/csv",
            "res_model": self._name,
            "res_id": self.id,
        })
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%d?download=true" % attachment.id,
            "target": "self",
        }

    def action_view_batches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("iTax Imports - %(period)s", period=self.display_name),
            "res_model": "ke.vat.itax.batch",
            "view_mode": "list,form",
            "domain": [("return_id", "=", self.id)],
            "context": {"default_return_id": self.id,
                        "default_company_id": self.company_id.id},
        }


class KeVatReturnLine(models.Model):
    _name = "ke.vat.return.line"
    _description = "Kenyan VAT Return Line"
    _order = "sequence, id"

    return_id = fields.Many2one(
        "ke.vat.return", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(
        related="return_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="return_id.currency_id")
    report_line_id = fields.Many2one(
        "account.report.line", string="Report Line", ondelete="set null",
        help="The definition this figure came from. Kept so a box can be "
             "opened back to the journal items behind it.")

    code = fields.Char(string="Box")
    name = fields.Char(required=True)
    level = fields.Integer(help="Indent depth, from the report line hierarchy.")
    sequence = fields.Integer(index=True)

    base_value = fields.Monetary(string="Base")
    tax_value = fields.Monetary(string="VAT")
    # Community's report columns are sparse: most lines carry a base figure or
    # a tax figure but not both. Without these, an empty box would be
    # indistinguishable from a box that genuinely totals zero.
    has_base = fields.Boolean()
    has_tax = fields.Boolean()

    # Rendered as text so a box that carries no figure reads as blank rather
    # than as 0.00. The numbers stay available on base_value / tax_value for
    # export and pivoting. Column sums are not wanted here in any case: VAT3
    # boxes are not additive down the page.
    base_display = fields.Char(
        string="Base", compute="_compute_displays")
    tax_display = fields.Char(
        string="VAT", compute="_compute_displays")

    @api.depends("base_value", "tax_value", "has_base", "has_tax",
                 "currency_id")
    def _compute_displays(self):
        for line in self:
            currency = line.currency_id
            line.base_display = formatLang(
                self.env, line.base_value, currency_obj=currency
            ) if line.has_base else ""
            line.tax_display = formatLang(
                self.env, line.tax_value, currency_obj=currency
            ) if line.has_tax else ""

    def action_drill(self):
        """Open the journal items behind this box.

        Aggregation boxes have no journal items of their own, so the dependency
        closure is walked down to the tax-tag leaves it is built from and their
        tags are unioned. Box 17 therefore opens everything that makes it up.
        """
        self.ensure_one()
        if not self.report_line_id:
            raise UserError(_(
                "This line is not linked to a report definition any more, so "
                "there is nothing to open."))

        expressions = self.report_line_id.expression_ids
        expressions |= expressions._expand_aggregations()
        tag_expressions = expressions.filtered(lambda e: e.engine == "tax_tags")

        if not tag_expressions:
            return self._drill_external(expressions)

        tags = tag_expressions._get_matching_tags()
        options = self.return_id._render_options()
        domain = self.env["ke.vat.render"]._aml_domain(
            self.return_id.report_id, options,
            options["date_from"], options["date_to"])
        domain &= Domain("tax_tag_ids", "in", tags.ids)

        # Community ships exactly the list view this wants and points nothing
        # at it: tax_line_id and tax_base_amount, with the base summed.
        audit_view = self.env.ref(
            "account.view_move_line_tax_audit_tree", raise_if_not_found=False)
        return {
            "type": "ir.actions.act_window",
            "name": _("%(box)s - %(period)s",
                      box=self.name, period=self.return_id.display_name),
            "res_model": "account.move.line",
            "views": [(audit_view.id if audit_view else False, "list"),
                      (False, "form")],
            "domain": list(domain),
            "context": {"create": False},
            "target": "current",
        }

    def _drill_external(self, expressions):
        """Boxes with no journal items open their stored values instead."""
        self.ensure_one()
        external = expressions.filtered(lambda e: e.engine == "external")
        if not external:
            raise UserError(_(
                "%(box)s is not built from journal items, so there is nothing "
                "to open.", box=self.name))
        return {
            "type": "ir.actions.act_window",
            "name": _("%(box)s - %(period)s",
                      box=self.name, period=self.return_id.display_name),
            "res_model": "account.report.external.value",
            "view_mode": "list,form",
            "domain": [("target_report_expression_id", "in", external.ids),
                       ("company_id", "=", self.company_id.id)],
            "context": {"create": False},
            "target": "current",
        }
