# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The Pull API as an OCA online statement provider.

The OCA framework brings the schedule, the "pull now" wizard, its debug view
and the error log. The rows do not become statement lines here: they go
through the receipt log like any C2B confirmation (``_pull_receive``), so the
receipts C2B or STK already brought change nothing, and the ones they missed
land in the till's daily statement exactly once.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .mpesa_transaction import masked

# A scheduled pull reaches back past the previous run by this much, for
# receipts Safaricom records late. With hourly runs: the last two hours.
PULL_OVERLAP = timedelta(hours=1)


class OnlineBankStatementProvider(models.Model):
    _inherit = "online.bank.statement.provider"

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [("mpesa_pull", "M-PESA Pull API")]

    def _mpesa_shortcode(self):
        self.ensure_one()
        shortcode = self.journal_id.mpesa_shortcode_ids[:1]
        if not shortcode:
            raise UserError(_("%s is not an M-Pesa journal.", self.journal_id.display_name))
        return shortcode

    def _pull(self, date_since, date_until):
        if self.env.context.get("scheduled") and len(self) == 1 and \
                self.service == "mpesa_pull" and self.last_successful_run:
            # Start the daily chunks early enough for the overlap: at 00:30 the
            # last two hours begin yesterday.
            date_since = min(date_since, self.last_successful_run - 2 * PULL_OVERLAP)
        if not self.env.context.get("scheduled") and not self.env.context.get("mpesa_backfill"):
            for provider in self.filtered(lambda p: p.service == "mpesa_pull"):
                reach = provider._mpesa_shortcode()._pull_window_start()
                if date_since < reach:
                    provider.message_post(body=_(
                        "The Pull API only reaches 48 hours back: for %(since)s to %(reach)s, "
                        "import the M-PESA Org Portal statement.",
                        since=date_since, reach=reach))
        return super()._pull(date_since, date_until)

    def _mpesa_pull_window(self, date_since, date_until):
        """The part of [date_since, date_until) the Pull API can and should
        answer for, or (None, None)."""
        self.ensure_one()
        now = fields.Datetime.now()
        since = max(date_since, self._mpesa_shortcode()._pull_window_start())
        if self.env.context.get("scheduled"):
            last = min(self.last_successful_run or now, now - PULL_OVERLAP)
            since = max(since, last - PULL_OVERLAP)
        until = min(date_until, now)
        if since >= until:
            return None, None
        return since, until

    def _obtain_statement_data(self, date_since, date_until):
        if self.service != "mpesa_pull":
            return super()._obtain_statement_data(date_since, date_until)
        self.ensure_one()
        since, until = self._mpesa_pull_window(date_since, date_until)
        if not since:
            return [], {}
        shortcode = self._mpesa_shortcode()
        rows = shortcode._pull_rows(since, until)
        if self.env.context.get("account_statement_online_import_debug"):
            return [dict(row, msisdn=masked(row.get("msisdn"))) for row in rows], {}
        shortcode._pull_receive(rows)
        # Nothing for the OCA framework to turn into lines: they exist already.
        return [], {}

    def _mpesa_backfill(self):
        """The last 48 hours, all of it: after setting up, or after an outage."""
        self.ensure_one()
        now = fields.Datetime.now()
        self.with_context(mpesa_backfill=True)._pull(
            self._mpesa_shortcode()._pull_window_start(), now)
        return True
