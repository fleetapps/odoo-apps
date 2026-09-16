# -*- coding: utf-8 -*-
"""Tie external values back to the return that owns them.

``account.report.external.value`` is the Community model behind every box a
person types into and behind the credit carried forward between periods. It
ships complete -- carryover fields, access rights, a company record rule -- and
with nothing in Community that reads or writes it.

The one thing it lacks for our purposes is a link back to the return. Without
it there is no way to show a return's six manual boxes as a simple editable
list, and no clean way to make closing idempotent: re-closing a period has to
replace exactly the carryover value that period wrote last time, not guess at it
by date and target.
"""

from odoo import fields, models


class AccountReportExternalValue(models.Model):
    _inherit = "account.report.external.value"

    ke_vat_return_id = fields.Many2one(
        "ke.vat.return",
        string="Kenyan VAT Return",
        index="btree_not_null",
        ondelete="set null",
        help="The return that created this value: either a box someone typed "
             "into, or the credit it carried forward when it was closed.",
    )
