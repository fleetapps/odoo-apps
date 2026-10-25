# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The Pull API on a shortcode: registration, and reading it back."""

import logging
import re
from datetime import UTC, timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.x_ke_mpesa.lib import daraja

_logger = logging.getLogger(__name__)

# A guard against an OffSetValue Daraja ignores: the same page forever.
PULL_MAX_PAGES = 200


class MpesaShortcode(models.Model):
    _inherit = "mpesa.shortcode"

    pull_number = fields.Char(
        "Pull API shortcode",
        help="The number the Pull API is registered for, when it is not this "
             "shortcode's own (a till whose store holds the registration).")
    pull_nominated_number = fields.Char(
        "Nominated number",
        help="The business's Safaricom number nominated for the Pull API, as agreed "
             "with Safaricom.")
    pull_registered_at = fields.Datetime("Pull API registered", readonly=True, copy=False)
    pull_provider_id = fields.Many2one(
        related="journal_id.online_bank_statement_provider_id", string="Pull schedule")
    pull_last_at = fields.Datetime("Last pull", readonly=True, copy=False)
    pull_last_rows = fields.Integer("Rows in the last pull", readonly=True, copy=False)
    pull_last_new = fields.Integer("Receipts the last pull added", readonly=True, copy=False)

    def _pull_shortcode(self):
        self.ensure_one()
        return (self.pull_number or self.number or "").strip()

    def _create_journal(self):
        journal = super()._create_journal()
        mapping = self.env.ref("x_ke_mpesa_statement.mapping_mpesa_org_portal",
                               raise_if_not_found=False)
        if mapping:
            journal.sudo().default_sheet_mapping_id = mapping
        return journal

    # ------------------------------------------------------------------
    # Setting up
    # ------------------------------------------------------------------

    def action_register_pull(self):
        """Register the Pull API (production, one-time, with the nominated number)."""
        for rec in self:
            sudo = rec.sudo()
            if not rec.pull_nominated_number:
                raise UserError(_("Enter the nominated number of %s first.", rec.display_name))
            url = sudo._callback_url("pull")
            problems = rec._url_problems(url)
            if problems and rec.state == "live":
                raise UserError("\n".join(problems))
            try:
                answer = sudo._client().pull_register(
                    shortcode=rec._pull_shortcode(),
                    nominated_number=daraja.normalize_msisdn(rec.pull_nominated_number)
                    or rec.pull_nominated_number,
                    callback_url=url,
                )
            except daraja.DarajaError as exc:
                if not re.search(r"already registered", exc.message or "", re.I):
                    raise UserError(_("Daraja refused the Pull registration: %s", exc)) from exc
                answer = {"ResponseDescription": exc.message}
            except daraja.DarajaTransportError as exc:
                raise UserError(_("Daraja could not be reached: %s", exc)) from exc
            rec.pull_registered_at = fields.Datetime.now()
            rec.message_post(body=_(
                "Pull API registered for %(code)s (%(answer)s).", code=rec._pull_shortcode(),
                answer=answer.get("ResponseDescription") or "OK"))
        return True

    def action_setup_pull(self):
        """Pull this shortcode every hour, and fetch the last 48 hours now."""
        Provider = self.env["online.bank.statement.provider"].sudo()
        for rec in self:
            journal = rec.journal_id
            if not journal:
                raise UserError(_("Set up the accounting of %s first.", rec.display_name))
            if rec.state == "live" and not rec.pull_registered_at:
                raise UserError(_("Register the Pull API of %s first.", rec.display_name))
            provider = Provider.with_context(active_test=False).search(
                [("journal_id", "=", journal.id)], limit=1)
            if provider and provider.service != "mpesa_pull":
                raise UserError(_(
                    "%(journal)s already takes statements from %(provider)s.",
                    journal=journal.display_name, provider=provider.display_name))
            if provider and not provider.active:
                provider.active = True
            if not provider:
                provider = Provider.create({
                    "journal_id": journal.id,
                    "service": "mpesa_pull",
                    "interval_type": "hours",
                    "interval_number": 1,
                    "statement_creation_mode": "daily",
                    "tz": "Africa/Nairobi",
                })
            provider.with_delay(
                description=_("M-Pesa: 48-hour Pull backfill of %s", rec.display_name),
            )._mpesa_backfill()
            rec.message_post(body=_(
                "Pulled every hour from now on; the last 48 hours are being fetched."))
        return True

    def action_import_statement(self):
        """The statement import wizard, on this till's journal, with the Org
        Portal mapping."""
        self.ensure_one()
        if not self.journal_id:
            raise UserError(_("Set up the accounting of %s first.", self.display_name))
        mapping = self.journal_id.default_sheet_mapping_id or self.env.ref(
            "x_ke_mpesa_statement.mapping_mpesa_org_portal")
        return {
            "type": "ir.actions.act_window",
            "name": _("Import M-PESA statement"),
            "res_model": "account.statement.import",
            "view_mode": "form",
            "target": "new",
            "context": {"journal_id": self.journal_id.id,
                        "default_sheet_mapping_id": mapping.id},
        }

    # ------------------------------------------------------------------
    # Reading it
    # ------------------------------------------------------------------

    def _pull_rows(self, since, until):
        """Every Pull API row between ``since`` and ``until`` (naive UTC)."""
        self.ensure_one()
        client = self.sudo()._client()
        start = since.replace(tzinfo=UTC)
        end = until.replace(tzinfo=UTC)
        rows, seen, offset = [], set(), 0
        for _page in range(PULL_MAX_PAGES):
            page = client.pull_query(shortcode=self._pull_shortcode(), start=start, end=end,
                                     offset=offset)
            fresh = []
            for row in page:
                key = (daraja.clean_receipt(row.get("transactionId")), str(row.get("amount")))
                if key not in seen:
                    seen.add(key)
                    fresh.append(row)
            if not fresh:
                break
            rows += fresh
            offset += len(page)
        else:
            _logger.warning("M-Pesa Pull of %s stopped after %d pages", self.number,
                            PULL_MAX_PAGES)
        return rows

    def _pull_receive(self, rows):
        """Book Pull API rows through the receipt log. Returns how many were new."""
        self.ensure_one()
        Tx = self.env["mpesa.transaction"].sudo()
        company = self.company_id
        new = failed = 0
        for row in rows:
            receipt = daraja.clean_receipt(row.get("transactionId"))
            known = Tx.search_count([("company_id", "=", company.id), ("receipt", "=", receipt)])
            try:
                with self.env.cr.savepoint():
                    tx = Tx._from_pull(self, row)
            except Exception:  # noqa: BLE001 - one bad row must not stop the rest
                _logger.exception("M-Pesa Pull row %s of %s failed", receipt, self.number)
                failed += 1
                continue
            if tx and not known:
                new += 1
        self.sudo().write({"pull_last_at": fields.Datetime.now(),
                           "pull_last_rows": len(rows), "pull_last_new": new})
        if new or failed:
            self.message_post(body=_(
                "Pull API: %(rows)s rows, %(new)s receipts Odoo did not have yet, "
                "%(failed)s that could not be booked (see the server log).",
                rows=len(rows), new=new, failed=failed))
        return new

    def _pull_window_start(self):
        """How far back the Pull API reaches from now."""
        return fields.Datetime.now() - timedelta(hours=48) + timedelta(minutes=10)
