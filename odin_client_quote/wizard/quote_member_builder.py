"""Quote a household: type who is covered, get the plans priced for them.

The agent is on a call and dictates "26 wife, 28 me, 13 brother". Pricing that
by hand means opening each plan's quotation and swapping the principal and
spouse onto the products for their age bands, because a template ships the
21-40 band -- the commonest family -- and nothing else. Four plans, two swaps
and a child count each, while somebody waits.

Band cover is not uniform either: the 500,000 to 3,000,000 cards price all six
bands, 5,000,000 stops at 75 and 7,500,000 and 10,000,000 stop at 70. A plan
with no rate for somebody's age is reported, never guessed at.
"""

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.odin_client_quote.models.quote_member import parse_roster

#: the band inside a product name, however it is spelled there
BAND_IN_NAME = re.compile(r"\((?:Day 1 to 20|\d{2}–\d{2}) Years\)")


class QuoteMemberBuilder(models.TransientModel):
    _name = "odin.quote.member.builder"
    _description = "Quote a household"

    partner_id = fields.Many2one("res.partner", string="Customer", required=True)
    comparison_id = fields.Many2one("odin.quote.comparison", readonly=True)
    roster = fields.Text(
        "Who is covered",
        help="One person per entry, separated by commas: 26 wife, 28 me, 13 brother. "
             "Either order works, and the words are only there to help -- the age is "
             "what prices it.")
    template_ids = fields.Many2many("sale.order.template", string="Plans to compare")
    line_ids = fields.One2many("odin.quote.member.builder.line", "builder_id", string="Members")
    summary = fields.Char(compute="_compute_summary")

    @api.depends("line_ids.role", "line_ids.age")
    def _compute_summary(self):
        for builder in self:
            counts = {}
            for line in builder.line_ids:
                counts[line.role] = counts.get(line.role, 0) + 1
            parts = []
            for role, label in (("principal", _("principal")), ("spouse", _("spouse")),
                                ("child", _("child")), ("other", _("not covered"))):
                if counts.get(role):
                    parts.append("%s %s" % (counts[role], label))
            builder.summary = ", ".join(parts) or _("nobody yet")

    @api.onchange("roster")
    def _onchange_roster(self):
        """Show the people as they are typed, so a wrong role is obvious."""
        for builder in self:
            if not builder.roster:
                continue
            builder.line_ids = [(5, 0, 0)] + [
                (0, 0, dict(person, sequence=(index + 1) * 10))
                for index, person in enumerate(parse_roster(builder.roster))
            ]

    # ------------------------------------------------------------------

    def action_build(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Type who is covered first."))
        if not self.template_ids:
            raise UserError(_("Pick at least one plan to compare."))
        if not self.line_ids.filtered(lambda line: line.role == "principal"):
            raise UserError(_("Mark one person as the principal: the plans are priced from their age."))

        comparison = self.comparison_id
        if not comparison:
            comparison = self.env["odin.quote.comparison"].create({
                "partner_id": self.partner_id.id,
                "title": _("Medical Insurance"),
            })
        comparison.member_ids.unlink()
        comparison.member_ids = [(0, 0, {
            "name": line.name, "age": line.age, "role": line.role,
            "sequence": line.sequence,
        }) for line in self.line_ids]

        comparison.template_ids = [(6, 0, self.template_ids.ids)]
        comparison.action_build_plans()

        notes = []
        for plan in comparison.plan_ids:
            notes += comparison._apply_members_to(plan.order_id)
        if notes:
            comparison.message_post(body=_(
                "Priced for %(who)s. Worth knowing:<ul>%(notes)s</ul>",
                who=self.summary,
                notes="".join("<li>%s</li>" % note for note in notes)))
        else:
            comparison.message_post(body=_("Priced for %s.", self.summary))
        return {
            "type": "ir.actions.act_window",
            "res_model": "odin.quote.comparison",
            "res_id": comparison.id,
            "views": [(False, "form")],
            "target": "current",
        }


class QuoteMemberBuilderLine(models.TransientModel):
    _name = "odin.quote.member.builder.line"
    _description = "A person being quoted for"
    _order = "sequence, id"

    builder_id = fields.Many2one(
        "odin.quote.member.builder", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char("Who", required=True)
    age = fields.Integer(required=True)
    role = fields.Selection(
        [("principal", "Principal"), ("spouse", "Spouse"), ("child", "Child"),
         ("other", "Not covered")], required=True, default="child")
    note = fields.Char(compute="_compute_note")

    @api.depends("age", "role")
    def _compute_note(self):
        from odoo.addons.odin_client_quote.models.quote_member import MAX_AGE, band_for
        for line in self:
            if line.age > MAX_AGE:
                line.note = _("Over %s: these plans do not cover them.", MAX_AGE)
            elif line.role == "other":
                line.note = _("No rate on these cards; the insurer quotes them separately.")
            else:
                line.note = band_for(line.age) or ""
