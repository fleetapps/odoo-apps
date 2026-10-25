# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import models


class AccountStatementImport(models.TransientModel):
    _inherit = "account.statement.import"

    def import_single_statement(self, single_statement_data, result):
        """Keep the Pull schedule of an M-Pesa journal: the OCA import switches
        the journal to "file import", which unlinks its online provider."""
        journal = self.env["account.journal"].browse(self.env.context.get("journal_id"))
        provider = journal.is_mpesa and journal.online_bank_statement_provider_id
        res = super().import_single_statement(single_statement_data, result)
        if provider and journal.online_bank_statement_provider_id != provider:
            journal.sudo().write({"bank_statements_source": "online",
                                  "online_bank_statement_provider": provider.service,
                                  "online_bank_statement_provider_id": provider.id})
        return res

    def _create_bank_statements(self, stmts_vals, result):
        """An Org Portal statement on an M-Pesa journal goes to its daily
        statements, row by row, instead of becoming a statement of its own."""
        Journal = self.env["account.journal"]
        mpesa = [st for st in stmts_vals
                 if Journal.browse(st.get("journal_id")).is_mpesa
                 and Journal.browse(st["journal_id"])._mpesa_is_statement(st)]
        if not mpesa:
            return super()._create_bank_statements(stmts_vals, result)
        others = [st for st in stmts_vals if st not in mpesa]
        if others:
            super()._create_bank_statements(others, result)
        touched = self.env["account.bank.statement"]
        for st_vals in mpesa:
            touched |= Journal.browse(st_vals["journal_id"])._mpesa_import_statement(
                st_vals, result)
        result["statement_ids"].extend(
            sid for sid in touched.ids if sid not in result["statement_ids"])
        return True
