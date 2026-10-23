"""Price a rate group for the people being covered.

A quotation template names one product of each rate group -- the 21-40 band,
Member Only -- because a template is written for nobody in particular. The
book finds the product each person actually falls under and how many of
each, and says plainly when the insurer has no rate for somebody rather than
quoting the nearest one.
"""

from collections import OrderedDict

from .rating import age_fits, role_fits


def role_label(env, role, count=1):
    """Who a quantity counts, in the user's language: Principal, Children."""
    if role == "principal":
        return env._("Principal")
    if role == "spouse":
        return env._("Spouse")
    if role == "child":
        return env._("Child") if count == 1 else env._("Children")
    return env._("Member") if count == 1 else env._("Members")


class PricedLine:
    """One product, how many, and for whom."""

    __slots__ = ("product_id", "qty", "price_unit", "members")

    def __init__(self, product, qty, price_unit, members=()):
        self.product_id = product
        self.qty = qty
        self.price_unit = price_unit
        self.members = list(members)

    @property
    def subtotal(self):
        return self.qty * self.price_unit

    def who(self):
        """Who the line is for: "Principal", "Children", "Family of 5".

        Blank when one rate covers people in different roles -- the adults
        on the 21-40 outpatient band -- where the count says enough.
        """
        env = self.product_id.env
        if self.product_id.odin_rate_basis == "family_size":
            count = len(self.members)
            return env._("Member only") if count == 1 else env._("Family of %s", count)
        roles = {member.role for member in self.members}
        if len(roles) == 1:
            return role_label(env, roles.pop(), self.qty)
        return ""


class RateBook:
    """The products of the catalogue, by rate group, for one company."""

    def __init__(self, env, company):
        self.env = env
        self.company = company
        self._groups = {}

    def siblings(self, product):
        """Every saleable product in ``product``'s rate group."""
        group = product.odin_rate_group
        if not group:
            return product
        if group not in self._groups:
            self._groups[group] = self.env["product.product"].search([
                ("odin_rate_group", "=", group),
                ("sale_ok", "=", True),
                ("company_id", "in", [False, self.company.id]),
            ], order="id")
        return self._groups[group] or product

    def price_unit(self, product):
        return product.with_company(self.company).lst_price

    def price(self, products, members, headcount=None):
        """Price the rate group of ``products`` for ``members``.

        ``products`` are the template's products of one rate group; their
        first one says how the group is priced. Returns ``(lines, reason)``:
        the priced lines, or no lines and why the group cannot be quoted for
        these people.
        """
        _ = self.env._
        first = products[:1]
        basis = first.odin_rate_basis or "policy"
        if basis == "policy":
            return [PricedLine(first, 1, self.price_unit(first))], ""

        siblings = self.siblings(first)
        if basis == "family_size":
            size = headcount if headcount is not None else len(members)
            # A family is rated on its eldest member: "Below 70" rates are for
            # families where nobody has reached 70.
            eldest = max((m.age for m in members), default=0)
            fitting = siblings.filtered(lambda p: age_fits(
                p.odin_rate_age_min, p.odin_rate_age_max, eldest))
            if not fitting:
                if first.odin_rate_age_min and eldest < first.odin_rate_age_min:
                    return [], _("For families with a member aged %s or over.",
                                 first.odin_rate_age_min)
                if first.odin_rate_age_max and eldest > first.odin_rate_age_max:
                    return [], _("For families where everyone is under %s.",
                                 first.odin_rate_age_max + 1)
                return [], _("No rate for a member aged %s.", eldest)
            exact = fitting.filtered(lambda p: p.odin_rate_family_size == size)[:1]
            if exact:
                return [PricedLine(exact, 1, self.price_unit(exact), members)], ""
            largest = max(fitting.mapped("odin_rate_family_size") or [0])
            if size > largest:
                return [], _("Rated for families of up to %s people.", largest)
            return [], _("No rate for a family of %s.", size)

        # Per person: the most specific rate for each of them -- their own
        # role before "anyone but the principal" before "anyone".
        lines = OrderedDict()
        for member in members:
            fitting = siblings.filtered(
                lambda p, m=member: role_fits(p.odin_rate_role or "any", m)
                and age_fits(p.odin_rate_age_min, p.odin_rate_age_max, m.age))
            if not fitting:
                if member.role == "child":
                    return [], _("No rate for children on this cover.")
                return [], _("No rate for the %(who)s at age %(age)s.",
                             who=role_label(self.env, member.role).lower(),
                             age=member.age)
            best = fitting.sorted(lambda p, m=member: (
                p.odin_rate_role != m.role,
                (p.odin_rate_role or "any") == "any",
                p.id,
            ))[:1]
            if best.id not in lines:
                lines[best.id] = PricedLine(best, 0, self.price_unit(best))
            lines[best.id].qty += 1
            lines[best.id].members.append(member)
        if not lines:
            return [], _("Nobody on this quote is covered by it.")
        return list(lines.values()), ""
