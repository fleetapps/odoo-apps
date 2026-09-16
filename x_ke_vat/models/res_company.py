# -*- coding: utf-8 -*-
"""Whether closing a VAT return also posts anything, and to where.

Closing a period always freezes its figures and sets the tax lock date. Whether
it also writes a journal entry is a decision the client's accountant owns, so it
is opt-in per company and off by default: the first thing anyone should do with
this module is run it in parallel against a period that has already been filed,
and that has to be possible without touching the ledger at all.

Tax and tag lookups are not here. They live on res.company in x_ke_base,
because the eTIMS transmitter resolves the same taxes and the same tags, and
two modules resolving them separately is how the wire and the return come to
disagree.
"""

from odoo import _, fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = "res.company"

    ke_vat_post_on_close = fields.Boolean(
        string="Post VAT entry on close",
        default=False,
        help="When enabled, closing a Kenyan VAT return also posts the VAT "
             "payable journal entry. Left off, closing only freezes the boxes "
             "and sets the tax lock date, so a parallel run against an "
             "already-filed period never touches the ledger.",
    )
    ke_vat_journal_id = fields.Many2one(
        "account.journal",
        string="VAT Journal",
        check_company=True,
        domain="[('type', '=', 'general')]",
    )
    ke_vat_output_account_id = fields.Many2one(
        "account.account", string="Output VAT Account", check_company=True)
    ke_vat_input_account_id = fields.Many2one(
        "account.account", string="Input VAT Account", check_company=True)
    ke_vat_payable_account_id = fields.Many2one(
        "account.account", string="VAT Payable Account", check_company=True)
    ke_vat_credit_account_id = fields.Many2one(
        "account.account", string="VAT Credit Account", check_company=True,
        help="Where a net credit lands when input VAT exceeds output VAT for "
             "the period.")
    ke_vat_non_deductible_account_id = fields.Many2one(
        "account.account", string="Non-deductible VAT Account", check_company=True,
        help="Expense account for the input VAT the return does not allow: "
             "box 14 (attributable to exempt supplies only) and box 16 (the "
             "apportioned share of mixed-use input VAT). Required only when "
             "either box is non-zero at close.")
    ke_vat_import_vat_account_id = fields.Many2one(
        "account.account", string="Import VAT Account", check_company=True,
        help="The account l10n_ke's import VAT (box 13, VAT on imported "
             "services) is posted to. Left empty, the Input VAT Account is "
             "used.")

    def _ke_vat_check_posting_config(self, needs_import=False,
                                     needs_non_deductible=False):
        """Refuse to post a VAT entry with the configuration half-filled.

        Named fields, not a generic message: an accountant told "configuration
        missing" has to go hunting, and the close action is exactly the wrong
        moment for that. The non-deductible account is only demanded when the
        period actually has a non-deductible amount; the import VAT account
        never is, because it defaults to the input VAT account.
        """
        self.ensure_one()
        required = {
            "ke_vat_journal_id": _("VAT Journal"),
            "ke_vat_output_account_id": _("Output VAT Account"),
            "ke_vat_input_account_id": _("Input VAT Account"),
            "ke_vat_payable_account_id": _("VAT Payable Account"),
            "ke_vat_credit_account_id": _("VAT Credit Account"),
        }
        if needs_non_deductible:
            required["ke_vat_non_deductible_account_id"] = _("Non-deductible VAT Account")
        missing = [label for field, label in required.items() if not self[field]]
        if missing:
            raise UserError(_(
                "This return is set to post a VAT entry on close, but "
                "%(company)s has not been given: %(missing)s.\n\n"
                "Set them under Settings, or turn off \"Post VAT entry on "
                "close\" to close without posting.",
                company=self.display_name,
                missing=", ".join(missing),
            ))
