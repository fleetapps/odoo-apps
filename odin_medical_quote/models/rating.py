"""How a premium product is priced, read from its name.

The rate cards name every product the way the insurer's sheet does, and the
name already says everything needed to price it for a family:

* ``Bizsure Afya Inpatient – Plan 1 (KES 200,000) – Member Only – Below 70``
  one product per *family size*: Member Only is one person, Member + 4 is five.
* ``Jamii Plus Inpatient (Family) – KES 500,000 – Principal (21–40 Years)``
  one product per *person*, chosen by who they are and their age band.
* ``Jamii Plus Outpatient – KES 100,000 per Person (41–54 Years)``
  per person, by age band, whoever they are.
* ``Hosi Cash Rider – Option 1 (KES 30,000) – Each Dependant``
  per person, the main member on one rate and everybody else on another.
* ``Jamii Plus Maternity – KES 50,000 per Family`` once per policy.

Products that differ only in those words -- the band, the role, the family
size -- are one *rate group*: the rows of the same table on the rate card.
A quotation template names one product of each group; the engine finds the
right sibling for each person from the group.

Everything here is a pure function of the name, so it is tested without a
database and the stored fields on ``product.template`` stay editable for the
product that does not follow the pattern.
"""

import re

#: "– Member Only" / "– Member + 4": the family-size products
SIZE_RE = re.compile(r"\s*[–—-]\s*Member\s+(?:(Only)|\+\s*(\d+))\b", re.I)
#: "(21–40 Years)" / "(Day 1 to 20 Years)"
BAND_RE = re.compile(
    r"\s*\(\s*(?:Day\s*1\s*to\s*(\d+)|(\d+)\s*[–—-]\s*(\d+))\s*Years?\s*\)", re.I)
#: "Below 70" / "Above 70": an eligibility band, kept in the name because the
#: two halves are different rate cards, not rows of one
BELOW_RE = re.compile(r"\bBelow\s+(\d+)\b", re.I)
ABOVE_RE = re.compile(r"\bAbove\s+(\d+)\b", re.I)
#: who a per-person rate is for, with the words that say so in the names
ROLES = (
    (re.compile(r"\s*[–—-]\s*Principal\b", re.I), "principal"),
    (re.compile(r"\s*[–—-]\s*Spouse\b", re.I), "spouse"),
    (re.compile(r"\s*[–—-]\s*Child\b(?!\s*Only)", re.I), "child"),
    (re.compile(r"\s*[–—-]\s*Main\s+Member\b", re.I), "principal"),
    (re.compile(r"\s*[–—-]\s*(?:Each\s+)?Dependants?\b", re.I), "dependant"),
)
#: "(Child Only)" is a cover for children alone: per child, and part of the
#: product's identity, so it is not stripped from the group
CHILD_ONLY_RE = re.compile(r"\(\s*Child(?:ren)?\s+Only\s*\)", re.I)
PER_PERSON_RE = re.compile(r"\bper\s+Person\b", re.I)
#: the first amount in the name: "KES 200,000", "(KES 30,000)"
LIMIT_RE = re.compile(r"\b[A-Z]{3}\s*(\d[\d,]*(?:\.\d+)?)")


def _tidy(text):
    """Collapse what stripping leaves behind: doubled dashes, stray spaces."""
    text = re.sub(r"\s+", " ", text or "")
    text = re.sub(r"(\s*[–—-]\s*){2,}", " – ", text)
    return text.strip(" –—-")


def parse_rate(name):
    """Read how a product is priced from its name.

    Returns a dict with:

    ``basis``       ``family_size``, ``person`` or ``policy``
    ``role``        ``principal``, ``spouse``, ``child``, ``dependant`` or ``any``
    ``age_min``     lowest age the rate applies to (0 when unbanded)
    ``age_max``     highest age, or 0 for no upper limit
    ``family_size`` people covered by a family-size product, else 0
    ``limit``       what the cover pays out, the first amount in the name
    ``group``       the name without the role, band and family size
    """
    name = name or ""
    group = name
    basis = "policy"
    role = "any"
    family_size = 0
    age_min = age_max = 0

    size = SIZE_RE.search(group)
    if size:
        family_size = 1 if size.group(1) else int(size.group(2)) + 1
        basis = "family_size"
        group = SIZE_RE.sub("", group, count=1)

    for pattern, value in ROLES:
        if pattern.search(group):
            role = value
            group = pattern.sub("", group, count=1)
            break

    band = BAND_RE.search(group)
    if band:
        if band.group(1):
            age_min, age_max = 0, int(band.group(1))
        else:
            age_min, age_max = int(band.group(2)), int(band.group(3))
        group = BAND_RE.sub("", group, count=1)
    else:
        below = BELOW_RE.search(name)
        above = ABOVE_RE.search(name)
        if below:
            age_max = int(below.group(1)) - 1
        elif above:
            age_min = int(above.group(1))

    if CHILD_ONLY_RE.search(name):
        role = "child"
    if basis != "family_size" and (
        role != "any" or band or PER_PERSON_RE.search(name)
    ):
        basis = "person"

    limit = 0.0
    found = LIMIT_RE.search(name)
    if found:
        limit = float(found.group(1).replace(",", ""))

    return {
        "basis": basis,
        "role": role,
        "age_min": age_min,
        "age_max": age_max,
        "family_size": family_size,
        "limit": limit,
        "group": _tidy(group),
    }


# -- who is covered ---------------------------------------------------------

#: a child on these cards is anyone in the "Day 1 to 20" band
CHILD_AGE = 20


class Member:
    """One person on the policy: who they are and how old."""

    __slots__ = ("role", "age", "label")

    def __init__(self, role, age, label):
        self.role = role
        self.age = age
        self.label = label

    def __repr__(self):
        return "Member(%s, %s)" % (self.role, self.age)


class Household:
    """The people a quotation is priced for.

    Children are counted, not dated: the rate cards price every child on one
    band (Day 1 to 20), so their exact ages do not change a premium. They are
    priced at age 0, which is inside that band on every card.
    """

    def __init__(self, principal_age=None, spouse_age=None, children=0):
        self.members = []
        if principal_age is not None:
            self.members.append(Member("principal", principal_age, "Principal"))
        if spouse_age is not None:
            self.members.append(Member("spouse", spouse_age, "Spouse"))
        for index in range(max(int(children or 0), 0)):
            self.members.append(Member("child", 0, "Child %s" % (index + 1)))

    @property
    def headcount(self):
        return len(self.members)

    @property
    def eldest(self):
        return max((member.age for member in self.members), default=0)

    @property
    def principal(self):
        return next((m for m in self.members if m.role == "principal"), None)

    @property
    def children(self):
        return [m for m in self.members if m.role == "child"]

    def adults(self):
        return [m for m in self.members if m.role != "child"]


def role_fits(rate_role, member):
    """Does a per-person rate for ``rate_role`` apply to ``member``?"""
    if rate_role == "any":
        return True
    if rate_role == "dependant":
        return member.role != "principal"
    return rate_role == member.role


def age_fits(age_min, age_max, age):
    """Is ``age`` inside the band? ``age_max`` 0 means no upper limit."""
    return age >= (age_min or 0) and (not age_max or age <= age_max)
