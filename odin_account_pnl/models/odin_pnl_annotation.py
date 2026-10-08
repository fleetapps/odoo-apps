"""Notes left on a P&L line or account, as Enterprise's "Annotate".

An annotation hangs off a layout line code and, optionally, one account, so it
stays attached when the layout is reordered. It is dated: the report shows the
notes whose date falls in the period on screen, and those without a date
always. The PDF prints them as footnotes.
"""

from odoo import fields, models


class OdinPnlAnnotation(models.Model):
    _name = "odin.pnl.annotation"
    _description = "P&L annotation"
    _order = "date desc, id desc"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True)
    layout_id = fields.Many2one("odin.pnl.layout", ondelete="cascade", index=True)
    line_code = fields.Char(required=True, index=True)
    account_id = fields.Many2one("account.account", ondelete="cascade", index=True)
    date = fields.Date(help="The period end the note is about. Empty: shown for every period.")
    note = fields.Text(required=True)
    author_id = fields.Many2one(
        "res.users", default=lambda self: self.env.user, readonly=True, required=True)
