import os
import re

from odoo import _, api, fields, models

from odoo.addons.odin_client_quote.models.sale_order_line import CODE_PREFIX

from .pricing import PricedLine

#: headings that collect fees and terms rather than cover
CHARGES = frozenset({"statutory_charges", "payment_options"})
#: "Optional – dental cover (per person, with outpatient cover)"
REQUIRES_OUTPATIENT = re.compile(r"\bwith\s+outpatient\b", re.I)
#: where a shared prefix may be cut: between the parts of a product name
SEPARATOR = re.compile(r"\s[–—-]\s")


def distinguishing_labels(names):
    """What tells each name apart from the others in its group.

    ``Femina Plus Rider – Silver / Gold / Platinum`` -> ``Silver``, ``Gold``,
    ``Platinum``. The shared prefix is only cut between the parts of a
    name, so ``Option A`` and ``Option B`` keep their word.
    """
    if len(names) < 2:
        return [""] * len(names)
    prefix = os.path.commonprefix(names)
    cut = max((m.end() for m in SEPARATOR.finditer(prefix)), default=0)
    return [name[cut:].strip(" –—-") or name for name in names]


class SaleOrderTemplate(models.Model):
    _inherit = "sale.order.template"

    odin_medical = fields.Boolean(
        string="Offer in medical quotes", compute="_compute_odin_plan", store=True,
        readonly=False,
        help="Lists this plan in Add Medical Quote. Ticked on its own for a "
        "template with an inpatient section.",
    )
    odin_insurer_id = fields.Many2one(
        "res.partner", string="Insurer",
        help="Who underwrites this plan. Medical quotes group and filter plans "
        "by insurer; left empty, the first part of the template name is used.",
    )
    odin_plan_type = fields.Selection(
        [("family", "Family"), ("child_only", "Children only")],
        string="Covers", compute="_compute_odin_plan", store=True, readonly=False,
        help="Children only: a policy for children without an adult on it.",
    )
    odin_inpatient_limit = fields.Monetary(
        string="Inpatient limit", compute="_compute_odin_plan", store=True,
        readonly=False, currency_field="odin_currency_id",
        help="The limit the inpatient limit range of a medical quote is "
        "matched against. Read from the cover limit of the inpatient section.",
    )
    odin_currency_id = fields.Many2one(
        "res.currency", compute="_compute_odin_currency_id")

    @api.depends("company_id")
    def _compute_odin_currency_id(self):
        for template in self:
            template.odin_currency_id = (
                template.company_id.currency_id or self.env.company.currency_id)

    @api.depends(
        "sale_order_template_line_ids.display_type",
        "sale_order_template_line_ids.benefit_key",
        "sale_order_template_line_ids.benefit_label",
        "sale_order_template_line_ids.is_optional",
        "sale_order_template_line_ids.cover_limit",
        "sale_order_template_line_ids.product_id.odin_rate_role",
        "sale_order_template_line_ids.product_id.odin_rate_limit",
    )
    def _compute_odin_plan(self):
        for template in self:
            blocks = template._odin_blocks()
            core = [block for block in blocks if block["kind"] == "core"]
            products = self.env["product.product"].concat(*[
                line.product_id for block in core
                for group in block["groups"] for line in group["lines"]
            ])
            template.odin_medical = any(block["key"] == "inpatient" for block in blocks)
            template.odin_plan_type = (
                "child_only" if products and all(
                    p.odin_rate_role == "child" for p in products)
                else "family")
            template.odin_inpatient_limit = next(
                (block["limit"] for block in core if block["limit"]), 0.0
            ) or max(products.mapped("odin_rate_limit") or [0.0])

    # -- names ------------------------------------------------------------

    def _odin_heading(self):
        """``APA Jamii Plus – Family Cover (KES 500,000)`` ->
        (``APA Jamii Plus``, ``Family Cover (KES 500,000)``): the product
        line and the plan, as the comparison report heads its columns."""
        self.ensure_one()
        name = self.name or ""
        head, sep, tail = name.partition("–")
        if not sep:
            head, sep, tail = name.partition(" - ")
        return head.strip() or name, tail.strip()

    def _odin_insurer_name(self):
        self.ensure_one()
        return self.odin_insurer_id.name or self._odin_heading()[0]

    # -- structure --------------------------------------------------------

    def _odin_blocks(self):
        """The template as the quote builder reads it, in template order.

        Each section opens a block: ``core`` cover is always part of the plan,
        ``optional`` cover is offered option by option, and ``charges`` are
        fees and terms carried as they are. Inside an optional section each
        subsection (Femina Plus, Hosi Cash, ...) is a group of its own, and
        each rate group in a group is one option the client can pick.
        """
        self.ensure_one()
        blocks = []
        block = group = None

        def start(kind, section=None):
            key = section._client_benefit_key() if section else "cover"
            label = section._odin_label() if section else _("Cover")
            return {
                "kind": kind,
                "section": section,
                "key": key,
                "label": label,
                "limit": section.cover_limit if section else 0.0,
                "requires": "outpatient" if section and REQUIRES_OUTPATIENT.search(
                    section.name or "") else "",
                "notes": [],
                "groups": [],
            }

        for line in self.sale_order_template_line_ids.sorted(lambda l: (l.sequence, l.id)):
            if line.display_type == "line_section":
                key = line._client_benefit_key()
                kind = ("charges" if key in CHARGES
                        else "optional" if line.is_optional else "core")
                block = start(kind, line)
                group = None
                blocks.append(block)
                continue
            if block is None:
                block = start("core")
                blocks.append(block)
            if line.display_type == "line_subsection":
                group = {"anchor": line, "label": line._odin_label(), "lines": []}
                block["groups"].append(group)
            elif line.display_type == "line_note":
                block["notes"].append(line)
            elif line.product_id and not line.product_id.odin_instalment_interest:
                if group is None:
                    group = {"anchor": block["section"] or line, "label": block["label"],
                             "lines": []}
                    block["groups"].append(group)
                group["lines"].append(line)

        # Keys that outlive the template's lines: a saved quote names the
        # options it picked, and regenerating the catalogue recreates the
        # lines with new ids. The benefit and the group's name do not change.
        used = set()
        for block in blocks:
            for group in block["groups"]:
                key = "%s/%s" % (block["key"], (group["label"] or "").strip().lower())
                candidate, count = key, 1
                while candidate in used:
                    count += 1
                    candidate = "%s#%s" % (key, count)
                used.add(candidate)
                group["key"] = candidate
        return blocks

    @staticmethod
    def _odin_rate_groups(group):
        """The template lines of a group, by rate group, in template order."""
        found = {}
        for line in group["lines"]:
            key = line.product_id.odin_rate_group or "product-%s" % line.product_id.id
            found.setdefault(key, []).append(line)
        return list(found.items())

    # -- priced for a family ----------------------------------------------

    def _odin_offer(self, household, book):
        """This plan priced for ``household``.

        Returns the core cover (always on the plan), every optional benefit
        with each of its options priced, and the charges. Each block keeps its
        own priced lines (``block["priced"]``) so the quotation can be laid
        out in the template's order. A plan the family cannot take comes back
        with ``available`` False and the reason, so the agent can tell the
        client why rather than wonder where it went.
        """
        self.ensure_one()
        child_only = self.odin_plan_type == "child_only"
        members = household.children if child_only else household.members
        offer = {
            "template": self,
            "available": True,
            "reason": "",
            "core": [],
            "groups": [],
            "charges": [],
            "blocks": self._odin_blocks(),
        }

        def refuse(reason):
            if offer["available"]:
                offer.update(available=False, reason=reason)

        if child_only and household.principal:
            refuse(_("Covers children only, without an adult."))
        elif not child_only and not household.principal:
            refuse(_("Covers a family: add the principal."))
        elif not members:
            refuse(_("Nobody to cover."))

        for block in offer["blocks"]:
            block["priced"] = []
            if block["kind"] == "charges":
                for group in block["groups"]:
                    for line in group["lines"]:
                        if line.product_uom_qty:
                            block["priced"].append(_charge(book, line))
                offer["charges"] += block["priced"]
            elif block["kind"] == "core":
                for group in block["groups"]:
                    for _key, lines in self._odin_rate_groups(group):
                        products = self.env["product.product"].concat(
                            *[line.product_id for line in lines])
                        priced, reason = book.price(products, members)
                        if reason:
                            refuse(reason)
                        block["priced"] += priced
                offer["core"] += block["priced"]
            else:
                block["offered"] = [
                    self._odin_offer_group(block, group, members, book)
                    for group in block["groups"]
                ]
                offer["groups"] += block["offered"]
        if not offer["core"] and offer["available"]:
            refuse(_("This plan has no inpatient cover to quote."))
        return offer

    def _odin_offer_group(self, block, group, members, book):
        options = []
        for rate_key, lines in self._odin_rate_groups(group):
            products = self.env["product.product"].concat(*[line.product_id for line in lines])
            priced, reason = book.price(products, members)
            first = products[:1]
            options.append({
                "key": "%s:%s" % (group["key"], rate_key),
                "rate_key": rate_key,
                "name": rate_key,
                "limit": first.odin_rate_limit,
                "basis": first.odin_rate_basis or "policy",
                "lines": priced,
                "amount": sum(line.subtotal for line in priced),
                "available": not reason,
                "reason": reason,
            })
        limits = [option["limit"] for option in options]
        by_limit = all(limits) and len(set(limits)) == len(limits)
        labels = distinguishing_labels([option["name"] for option in options])
        for option, label in zip(options, labels):
            # A limit says it all when every option has a different one --
            # "Limit 50,000" -- otherwise the words that differ do.
            option["label"] = "" if by_limit else (label or option["name"])
        return {
            "key": group["key"],
            "label": group["label"],
            "benefit_key": block["key"],
            "requires": block["requires"],
            "basis": options[0]["basis"] if options else "policy",
            "block": block,
            "group": group,
            "options": options,
        }


def _charge(book, line):
    return PricedLine(line.product_id, line.product_uom_qty, book.price_unit(line.product_id))


class SaleOrderTemplateLine(models.Model):
    _inherit = "sale.order.template.line"

    def _client_benefit_key(self):
        """Same key the client reports match a benefit on across plans."""
        self.ensure_one()
        key = self.benefit_key or self.benefit_label or self.name or ""
        return key.strip().lower()

    def _odin_label(self):
        self.ensure_one()
        return self.benefit_label or CODE_PREFIX.sub("", self.name or "").strip()
