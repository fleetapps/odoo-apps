# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, api, fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    mpesa_shortcode_ids = fields.One2many("mpesa.shortcode", "journal_id",
                                          string="M-Pesa shortcodes")
    is_mpesa = fields.Boolean("M-Pesa", compute="_compute_is_mpesa", store=True)

    @api.depends("mpesa_shortcode_ids")
    def _compute_is_mpesa(self):
        for journal in self:
            journal.is_mpesa = bool(journal.mpesa_shortcode_ids)

    def _statement_line_import_update_unique_import_id(self, st_line_vals, account_number):
        """Keep ``MPESA-<receipt>`` as is on M-Pesa journals.

        account_statement_import_base prefixes every imported id with the
        account number and journal id. That would let the same receipt in, a
        second time, from a statement file or the Pull API after the C2B
        confirmation already booked it: the canonical id is what makes the
        four channels collide.
        """
        unique_import_id = st_line_vals.get("unique_import_id") or ""
        if self.is_mpesa and unique_import_id.startswith("MPESA-"):
            return
        return super()._statement_line_import_update_unique_import_id(
            st_line_vals, account_number)

    def _mpesa_daily_statement(self, date):
        """The statement holding this journal's M-Pesa lines for ``date``."""
        self.ensure_one()
        name = _("%(journal)s %(date)s", journal=self.code, date=fields.Date.to_string(date))
        Statement = self.env["account.bank.statement"].sudo()
        statement = Statement.search(
            [("journal_id", "=", self.id), ("name", "=", name)], limit=1)
        return statement or Statement.create({"journal_id": self.id, "name": name})
