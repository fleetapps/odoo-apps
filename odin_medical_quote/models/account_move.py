import re

from odoo import _, fields, models
from odoo.tools import format_date


class AccountMove(models.Model):
    _inherit = "account.move"

    odin_quote_comparison_id = fields.Many2one(
        "odin.quote.comparison", string="Insurance quote",
        compute="_compute_odin_quote_comparison_id",
        help="The quote the invoiced plan was accepted from.")

    def _compute_odin_quote_comparison_id(self):
        Comparison = self.env["odin.quote.comparison"].sudo()
        for move in self:
            orders = move.invoice_line_ids.sale_line_ids.order_id
            move.odin_quote_comparison_id = Comparison.search(
                [("plan_ids.order_id", "in", orders.ids)], limit=1) if orders else False

    # -- the insurance invoice (report) ------------------------------------

    def _odin_invoice_date(self, value):
        return format_date(self.env, value, date_format="d MMM y") if value else ""

    def _odin_invoice_kicker(self):
        self.ensure_one()
        if self.move_type == "out_refund":
            return _("Credit note")
        return _("Draft invoice") if self.state == "draft" else _("Invoice")

    def _odin_invoice_rows(self):
        """The invoice's lines as the client reads them: the plan's headings,
        each priced line, no internal notes."""
        self.ensure_one()
        rows = []
        for line in self.invoice_line_ids.sorted(lambda l: (l.sequence, l.id)):
            if line.display_type in ("line_section", "line_subsection"):
                rows.append({"kind": "section", "label": line.name or ""})
            elif line.display_type == "product":
                # "[FEE-STAMP] Stamp duty\nhow it is priced": the code is the
                # office's, the rest is the client's.
                first, _sep, rest = (line.name or "").partition("\n")
                rows.append({"kind": "line", "label": re.sub(r"^\[[^\]]*\]\s*", "", first),
                             "detail": rest.strip(), "amount": line.price_subtotal})
        # A heading with nothing priced under it says nothing.
        return [row for index, row in enumerate(rows)
                if row["kind"] == "line"
                or (index + 1 < len(rows) and rows[index + 1]["kind"] == "line")]

    def _odin_invoice_instalments(self):
        """The due dates, when the invoice is paid in instalments."""
        self.ensure_one()
        terms = self.line_ids.filtered(lambda l: l.display_type == "payment_term").sorted(
            "date_maturity")
        if len(terms) < 2:
            return []
        sign = -1 if self.move_type == "out_refund" else 1
        return [{"number": index + 1, "date": self._odin_invoice_date(line.date_maturity),
                 "amount": sign * line.amount_currency}
                for index, line in enumerate(terms)]
