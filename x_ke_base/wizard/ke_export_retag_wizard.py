# -*- coding: utf-8 -*-
"""Correcting supplies that were already posted under the wrong tag.

``ke_apply_tax_corrections`` re-points the tax, which fixes every invoice posted
from then on. It cannot fix the ones posted before: tags are stamped onto
journal items when a move is posted, and they stay stamped. Those exports keep
reporting in box 4 instead of box 3.

Rewriting posted tax history is not something an install script gets to decide,
so it happens here, in front of an accountant, and only on their instruction.

The tax definition itself is normally corrected without anyone's help -- on
install, and again whenever the Kenyan chart of accounts is loaded. Because an
``l10n_ke`` upgrade can still put the defect back, the wizard also shows whether
the definition is currently correct and offers to re-apply the correction, so a
reverted database is repaired from this screen rather than from a shell.

Two properties matter more than convenience. The wizard reports before it
changes anything, broken down by period, so the size of the correction is known
before it is made. And it treats a locked period as a result rather than an
error: Odoo refuses to change a tax figure at or before the tax lock date, which
is exactly right, so those periods are listed as skipped instead of aborting the
run for everything else.

No tag is named here. Both the tag it moves away from and the tag it moves to
come from ke.tax.classification, so the same rename that fixes the renderer
fixes this too.
"""

import logging
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import RedirectWarning, UserError

from ..models.ke_tax_correction import RETAGGED_TAXES

_logger = logging.getLogger(__name__)


class KeTaxRetagWizard(models.TransientModel):
    _name = "ke.tax.retag.wizard"
    _description = "Repair Kenyan Tax Tags on Posted Entries"

    company_id = fields.Many2one(
        "res.company", required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")

    line_count = fields.Integer(
        string="Journal Items", compute="_compute_affected")
    move_count = fields.Integer(
        string="Invoices", compute="_compute_affected")
    affected_amount = fields.Monetary(
        string="Turnover Affected", compute="_compute_affected",
        help="Turnover that moves between VAT3 boxes if you proceed.")
    summary = fields.Text(compute="_compute_affected")

    # Whether the tax definition itself is right. Distinct from the counts
    # above: those are posted entries stamped while it was wrong.
    tax_ok = fields.Boolean(compute="_compute_tax_status")
    tax_status = fields.Text(compute="_compute_tax_status")

    # -------------------------------------------------------- tax definition

    @api.depends("company_id")
    def _compute_tax_status(self):
        Correction = self.env["ke.tax.correction"]
        for wizard in self:
            problems = Correction.verify(companies=wizard.company_id)
            wizard.tax_ok = not problems
            wizard.tax_status = "\n\n".join(problems) if problems else _(
                "The Kenyan tax definition on %(company)s carries every "
                "correction this module makes.",
                company=wizard.company_id.display_name)

    def action_apply_corrections(self):
        """Re-point the taxes, then come back to this screen with fresh counts.

        Only the tax corrections are applied here. A reverted report
        expression (box 16, box 20, box 26) is module data and comes back with
        an upgrade of Kenya Tax Base, which the status text says.
        """
        self.ensure_one()
        results = self.company_id.ke_apply_tax_corrections()
        outcome = results[0] if results else {"changes": [], "skipped": []}
        if outcome["changes"]:
            message = _("Applied: %(changes)s", changes="; ".join(outcome["changes"]))
        else:
            message = _("Nothing to change: %(skipped)s",
                        skipped="; ".join(outcome["skipped"]) or _("no Kenyan tax found"))
        # Expression corrections cannot be applied from here; say so rather
        # than leaving the status text to imply the button did it.
        if self.env["ke.tax.correction"]._verify_expressions(None):
            message += _(
                "\n\nA report expression is still reverted. Upgrade Kenya Tax "
                "Base to restore it.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Kenyan tax corrections"),
                "message": message,
                "type": "success" if outcome["changes"] else "info",
                "sticky": False,
                # Reopen so the status and the counts reflect the new state.
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "ke.tax.retag.wizard",
                    "view_mode": "form",
                    "target": "new",
                    "context": {"default_company_id": self.company_id.id},
                },
            },
        }

    # ------------------------------------------------------------- scanning

    def _corrections(self):
        """The (tax, wrong tag, right tag) triples that still need applying."""
        self.ensure_one()
        Classification = self.env["ke.tax.classification"]
        company = self.company_id
        out = []
        for template_id, spec in RETAGGED_TAXES.items():
            tax = company._ke_tax(template_id)
            wrong = company._ke_tag(Classification.tag_name_for(
                spec["wrong_box"], spec["side"], spec["label"]))
            right = company._ke_tag(Classification.tag_name_for(
                spec["right_box"], spec["side"], spec["label"]))
            if tax and wrong and right:
                out.append((tax, wrong, right))
        return out

    def _affected_lines(self):
        """Posted journal items still carrying a tag a correction moves away from."""
        self.ensure_one()
        lines = self.env["account.move.line"]
        for tax, wrong, _right in self._corrections():
            lines |= self.env["account.move.line"].search([
                ("company_id", "=", self.company_id.id),
                ("parent_state", "=", "posted"),
                ("tax_ids", "in", tax.ids),
                ("tax_tag_ids", "in", wrong.ids),
            ])
        return lines

    @api.depends("company_id")
    def _compute_affected(self):
        for wizard in self:
            lines = wizard._affected_lines()
            wizard.line_count = len(lines)
            wizard.move_count = len(lines.mapped("move_id"))
            # Sales are credits, so the balance is negative; report the figure
            # the way the return shows it.
            wizard.affected_amount = -sum(lines.mapped("balance"))
            wizard.summary = wizard._build_summary(lines)

    def _build_summary(self, lines):
        """Period by period, with the tax lock date counted honestly."""
        self.ensure_one()
        if not lines:
            return _(
                "Nothing to correct. No posted entry on this company carries a "
                "tag the Kenyan corrections move.")

        company = self.company_id
        lock_date = company.tax_lock_date
        # Counted per item rather than per period: a lock date set mid-month
        # locks part of a period, and rounding that up to "the whole period is
        # locked" would overstate what this wizard is about to refuse to touch.
        by_period = defaultdict(lambda: [0, 0.0, 0])
        for line in lines:
            key = line.date.strftime("%Y-%m")
            by_period[key][0] += 1
            by_period[key][1] -= line.balance
            if lock_date and line.date <= lock_date:
                by_period[key][2] += 1

        rows = []
        for period in sorted(by_period):
            count, amount, locked = by_period[period]
            rows.append(_(
                "  %(period)s: %(count)s item(s), %(amount)s%(locked)s",
                period=period, count=count,
                amount=company.currency_id.round(amount),
                locked=_("  [%(n)s locked, will be skipped]", n=locked)
                       if locked else ""))

        moves = ", ".join(
            "%s -> %s" % (wrong.name, right.name)
            for _tax, wrong, right in self._corrections())
        header = _(
            "These posted entries carry a tag that belongs to a different VAT3 "
            "box (%(moves)s):", moves=moves)
        footer = _(
            "\nRetagging changes a tax figure, so Odoo refuses it at or before "
            "the tax lock date (%(lock)s). Locked periods are counted above and "
            "will be left alone.",
            lock=lock_date or _("not set"))
        return "\n".join([header, ""] + rows + [footer])

    # ------------------------------------------------------------- actions

    def action_open_lines(self):
        """Look at the journal items before deciding."""
        self.ensure_one()
        lines = self._affected_lines()
        if not lines:
            raise UserError(_("There is nothing to correct."))
        audit_view = self.env.ref(
            "account.view_move_line_tax_audit_tree", raise_if_not_found=False)
        return {
            "type": "ir.actions.act_window",
            "name": _("Entries tagged to the wrong VAT3 box"),
            "res_model": "account.move.line",
            "views": [(audit_view.id if audit_view else False, "list"),
                      (False, "form")],
            "domain": [("id", "in", lines.ids)],
            "context": {"create": False},
            "target": "current",
        }

    def action_retag(self):
        """Move the affected items onto the tag their box actually reads."""
        self.ensure_one()
        corrections = self._corrections()
        if not corrections:
            raise UserError(_(
                "The Kenyan classification rows or taxes are not in place on "
                "%(company)s, so there is nothing to retag to.",
                company=self.company_id.display_name))

        retagged, skipped = 0, defaultdict(int)
        for tax, wrong, right in corrections:
            lines = self.env["account.move.line"].search([
                ("company_id", "=", self.company_id.id),
                ("parent_state", "=", "posted"),
                ("tax_ids", "in", tax.ids),
                ("tax_tag_ids", "in", wrong.ids),
            ])
            # One move at a time: a locked period must not take the rest down
            # with it, and Odoo raises per move when the lock date is crossed.
            for move in lines.mapped("move_id"):
                move_lines = lines.filtered(lambda l, m=move: l.move_id == m)
                try:
                    with self.env.cr.savepoint():
                        move_lines.write({
                            "tax_tag_ids": [(3, wrong.id), (4, right.id)],
                        })
                except (UserError, RedirectWarning):
                    skipped[move.date.strftime("%Y-%m")] += len(move_lines)
                    continue
                retagged += len(move_lines)

        _logger.info(
            "x_ke_base: retagged %d journal item(s) for %s, skipped %d in "
            "locked periods", retagged, self.company_id.display_name,
            sum(skipped.values()))

        if skipped:
            message = _(
                "Retagged %(done)s journal item(s). %(left)s were left alone "
                "because their period is closed to tax changes: %(periods)s.\n\n"
                "Those periods have already been declared. Correct them with "
                "KRA rather than by rewriting them here.",
                done=retagged, left=sum(skipped.values()),
                periods=", ".join(sorted(skipped)))
        else:
            message = _(
                "Retagged %(done)s journal item(s). Recompute any draft return "
                "for an affected period.", done=retagged)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Kenyan tax tags corrected"),
                "message": message,
                "type": "warning" if skipped else "success",
                "sticky": True,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
