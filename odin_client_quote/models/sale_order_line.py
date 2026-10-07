import re

from odoo import api, fields, models

#: ``[JP-IP3M-P21] Jamii Plus Inpatient ...`` -> ``Jamii Plus Inpatient ...``
CODE_PREFIX = re.compile(r"^\s*\[[^\]]+\]\s*")


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    cover_limit = fields.Monetary(
        string="Cover limit",
        currency_field="currency_id",
        help="What this benefit pays out, as opposed to what it costs. Set it on "
        "the section line: client reports print it as the section's limit row, "
        "and the comparison report lines it up across plans.",
    )
    benefit_label = fields.Char(
        string="Benefit name",
        help="Short name for this section in client reports, e.g. 'Outpatient'. "
        "Falls back to the section title, which is usually too long to head a "
        "comparison column.",
    )
    benefit_key = fields.Char(
        string="Benefit key",
        help="Matches this section to the same benefit on other plans in a cover "
        "comparison, e.g. 'inpatient'. Falls back to the benefit name, so plans "
        "that already name a benefit the same way line up on their own.",
    )
    client_name = fields.Char(
        string="Description (client)",
        compute="_compute_client_name",
        help="The line description with the internal product code removed.",
    )

    @api.depends("name")
    def _compute_client_name(self):
        for line in self:
            line.client_name = CODE_PREFIX.sub("", line.name or "").strip()

    def _get_sale_order_line_multiline_description_sale(self):
        """Describe the line without the internal product code.

        Odoo builds a line description from the product's display name, which
        carries the ``[BZ70-IP-P1-M+1]`` reference. That is the right thing in a
        warehouse and the wrong thing on a quotation a customer reads, and it
        also reaches them through the portal, where no report formatting can
        strip it. ``display_default_code`` is the framework's own switch for
        this, so the code is simply never written into the description.

        Existing lines keep whatever description they were created with; both
        client reports strip the prefix when printing, so old and new agree.
        """
        return super(
            SaleOrderLine, self.with_context(display_default_code=False)
        )._get_sale_order_line_multiline_description_sale()

    def _client_benefit_key(self):
        """Key this section is matched on across the plans of a comparison."""
        self.ensure_one()
        key = self.benefit_key or self.benefit_label or self.name or ""
        return key.strip().lower()

    def _client_benefit_label(self):
        """Short, client-facing name for this section."""
        self.ensure_one()
        return self.benefit_label or CODE_PREFIX.sub("", self.name or "").strip()
