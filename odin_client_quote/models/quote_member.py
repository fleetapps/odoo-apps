"""Who is being covered, and which product prices each of them.

The rate cards price a household member by member: a principal and a spouse in
one of six age bands, and children on a flat child rate. A template ships the
21-40 band for all three at quantity one, because that is the commonest
family -- so quoting a 55-year-old principal is not a matter of setting a
quantity, it is swapping the line onto the product for their band. That is the
work this does.

Adults who are neither the principal nor the spouse -- a parent, usually --
have no product in these cards at all. "Dependant" appears only on the Hosi
Cash rider, never on core cover. They are recorded and reported as not covered
rather than priced at a number the insurer never quoted.
"""

import re

from odoo import _, api, fields, models

#: age -> the band exactly as the product names spell it, en dashes included
BANDS = (
    (0, 20, "Day 1 to 20 Years"),
    (21, 40, "21–40 Years"),
    (41, 54, "41–54 Years"),
    (55, 65, "55–65 Years"),
    (66, 70, "66–70 Years"),
    (71, 75, "71–75 Years"),
    (76, 80, "76–80 Years"),
)
#: the cards stop at 80; the templates say so in as many words
MAX_AGE = 80
#: a child on these cards is anyone inside the child band
CHILD_AGE = 20

#: words that say who someone is, so a roster can be typed as it is spoken
SPOUSE_WORDS = ("wife", "husband", "spouse", "partner", "mrs", "mr")
SELF_WORDS = ("me", "self", "myself", "principal", "main", "i")
CHILD_WORDS = ("son", "daughter", "child", "kid", "brother", "sister", "sibling",
               "baby", "boy", "girl", "dependant", "dependent")


def band_for(age):
    """The product band an age falls in, or False past the cards' limit."""
    for low, high, label in BANDS:
        if low <= age <= high:
            return label
    return False


def parse_roster(text):
    """Turn "26 wife, 28 me, 13 brother" into [{age, label, role}, ...].

    Accepts the order either way round ("wife 26" and "26 wife"), separated by
    commas, semicolons or newlines, because that is how it gets dictated on a
    call. A roster without a stated principal takes the eldest adult, which is
    the usual arrangement and visible for the agent to change.
    """
    people = []
    for chunk in re.split(r"[,;\n]+", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        found = re.search(r"\d{1,3}", chunk)
        if not found:
            continue
        age = int(found.group())
        label = re.sub(r"\d{1,3}", "", chunk).strip(" -–—:\t") or _("Member")
        words = re.findall(r"[a-z']+", label.lower())
        if age <= CHILD_AGE:
            role = "child"
        elif any(word in SELF_WORDS for word in words):
            role = "principal"
        elif any(word in SPOUSE_WORDS for word in words):
            role = "spouse"
        elif any(word in CHILD_WORDS for word in words):
            role = "child"
        else:
            role = "other"
        people.append({"name": label, "age": age, "role": role})

    # Nobody said which one is the principal: the eldest adult is the usual
    # arrangement, and the agent can see it and change it.
    if people and not any(p["role"] == "principal" for p in people):
        adults = [p for p in people if p["age"] > CHILD_AGE]
        if adults:
            max(adults, key=lambda p: p["age"])["role"] = "principal"
    # A second person marked as the principal is a spouse, not two principals.
    seen = False
    for person in people:
        if person["role"] == "principal":
            if seen:
                person["role"] = "spouse"
            seen = True
    return people


class QuoteComparisonMember(models.Model):
    _name = "odin.quote.comparison.member"
    _description = "A person covered by a comparison"
    _order = "sequence, id"

    comparison_id = fields.Many2one(
        "odin.quote.comparison", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char("Who", required=True)
    age = fields.Integer(required=True)
    role = fields.Selection(
        [("principal", "Principal"), ("spouse", "Spouse"), ("child", "Child"),
         ("other", "Not covered")], required=True, default="child")
    band = fields.Char(compute="_compute_band", store=True)
    covered = fields.Boolean(compute="_compute_band", store=True)
    reason = fields.Char(compute="_compute_band", store=True)

    @api.depends("age", "role")
    def _compute_band(self):
        for member in self:
            member.band = band_for(member.age) or ""
            if member.age > MAX_AGE:
                member.covered, member.reason = False, _(
                    "These plans cover up to age %s.", MAX_AGE)
            elif member.role == "other":
                member.covered, member.reason = False, _(
                    "An adult who is neither the principal nor the spouse has no rate on "
                    "these cards: the insurer quotes them separately.")
            else:
                member.covered, member.reason = True, ""
