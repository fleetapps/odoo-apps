from odoo import api, fields, models

from .rating import parse_rate

RATE_BASIS = [
    ("policy", "Once per policy"),
    ("family_size", "By family size"),
    ("person", "Per person"),
]
RATE_ROLE = [
    ("any", "Anyone"),
    ("principal", "Principal"),
    ("spouse", "Spouse"),
    ("child", "Child"),
    ("dependant", "Anyone but the principal"),
]


class ProductTemplate(models.Model):
    """How a premium is priced for a family.

    Read from the product name when it is set (see ``rating.parse_rate``),
    and stored, so the quote builder can find the right product for each
    person with one search. Editable: a product whose name does not follow
    the rate card's wording is put right here rather than renamed.
    """

    _inherit = "product.template"

    odin_rate_basis = fields.Selection(
        RATE_BASIS, string="Priced", compute="_compute_odin_rate", store=True,
        readonly=False,
        help="Once per policy: quantity one. By family size: one product per "
        "number of people covered (Member Only, Member + 1, ...). Per person: "
        "one line per person covered, the product picked by who they are and "
        "their age.",
    )
    odin_rate_role = fields.Selection(
        RATE_ROLE, string="Rate for", compute="_compute_odin_rate", store=True,
        readonly=False,
    )
    odin_rate_age_min = fields.Integer(
        string="From age", compute="_compute_odin_rate", store=True, readonly=False,
    )
    odin_rate_age_max = fields.Integer(
        string="To age", compute="_compute_odin_rate", store=True, readonly=False,
        help="0 means no upper age.",
    )
    odin_rate_family_size = fields.Integer(
        string="People covered", compute="_compute_odin_rate", store=True,
        readonly=False,
        help="For a product priced by family size: Member Only is 1, "
        "Member + 4 is 5.",
    )
    odin_rate_limit = fields.Monetary(
        string="Cover limit", compute="_compute_odin_rate", store=True,
        readonly=False, currency_field="currency_id",
        help="What this cover pays out, shown beside its premium in the quote "
        "builder.",
    )
    odin_rate_group = fields.Char(
        string="Rate group", compute="_compute_odin_rate", store=True,
        readonly=False, index=True,
        help="Products with the same rate group are rows of one table on the "
        "rate card -- the same cover for different people. The quote builder "
        "picks the row for each person from the group.",
    )

    @api.depends("name")
    def _compute_odin_rate(self):
        for template in self:
            # Parsed in English: the patterns are the rate card's wording,
            # and a translated name would read differently per user.
            rate = parse_rate(template.with_context(lang="en_US").name)
            template.odin_rate_basis = rate["basis"]
            template.odin_rate_role = rate["role"]
            template.odin_rate_age_min = rate["age_min"]
            template.odin_rate_age_max = rate["age_max"]
            template.odin_rate_family_size = rate["family_size"]
            template.odin_rate_limit = rate["limit"]
            template.odin_rate_group = rate["group"]
