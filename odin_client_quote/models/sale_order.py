from odoo import _, api, fields, models

#: Studio/optional fields the client reports will show in the "details" strip
#: when the database happens to have them. Absent fields are skipped.
DETAIL_FIELDS = (
    "x_age_principal",
    "x_age_spouse",
    "x_children",
    "x_op_limit",
)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    odin_instalment_plan_ids = fields.Many2many(
        "odin.quote.instalment.plan",
        string="Instalment options",
        default=lambda self: self.env["odin.quote.instalment.plan"].search(
            [("company_id", "=", self.env.company.id)]
        ),
        help="Printed on the client quotation as a monthly alternative to "
        "paying the premium in full. Clear it to quote the annual premium only.",
    )

    def _client_instalments(self):
        """Each instalment option worked out on this quotation's cash premium."""
        self.ensure_one()
        total = self._client_amounts()["total"]
        return [plan.schedule(total) for plan in self.odin_instalment_plan_ids
                if plan._odin_available(total)]

    # -- amounts ----------------------------------------------------------

    def _client_financing_lines(self):
        """Lines that carry the cost of paying by instalment, not cover."""
        return self.order_line.filtered(
            lambda line: not line.display_type
            and line.product_id.odin_instalment_interest
        )

    def _client_amounts(self):
        """The premium as the client should read it: cash price, no financing.

        An instalment schedule is worked out from this, so a quotation that
        already carries an instalment charge is not charged interest twice.
        """
        self.ensure_one()
        financing = self._client_financing_lines()
        untaxed = self.amount_untaxed - sum(financing.mapped("price_subtotal"))
        total = self.amount_total - sum(financing.mapped("price_total"))
        return {
            "untaxed": untaxed,
            "tax": total - untaxed,
            "total": total,
            "financed": self.amount_total - total,
        }

    # -- structure --------------------------------------------------------

    #: headings that collect fees and terms rather than cover
    NOT_A_BENEFIT = frozenset({"statutory_charges", "payment_options"})

    @staticmethod
    def _is_charge(line):
        """A statutory fee or financing cost, which does belong under them."""
        code = line.product_id.default_code or ""
        return code.startswith("FEE") or line.product_id.odin_instalment_interest

    def _client_sections(self):
        """The quotation grouped the way a client reads it.

        Returns a list of sections, each ``{key, label, title, limit, included,
        optional, notes, amount}``. ``included`` are the lines actually being
        quoted; ``optional`` are the lines left at zero quantity, which belong
        in a price list of add-ons rather than in the bill.
        """
        self.ensure_one()
        sections = []
        current = None
        other = None

        def start(label, title="", limit=0.0, key=""):
            section = {
                "key": key or (label or "").strip().lower(),
                "label": label,
                "title": title,
                "limit": limit,
                "included": [],
                "optional": [],
                "notes": [],
                "amount": 0.0,
            }
            sections.append(section)
            return section

        for line in self.order_line.sorted(lambda sol: (sol.sequence, sol.id)):
            if line.display_type == "line_section":
                current = start(
                    line._client_benefit_label(),
                    title=line.client_name,
                    limit=line.cover_limit,
                    key=line._client_benefit_key(),
                )
            elif line.display_type == "line_subsection":
                if current is None:
                    current = start("Cover")
                current["optional"].append({"subsection": line.client_name})
            elif line.display_type == "line_note":
                if current is None:
                    current = start("Cover")
                current["notes"].append(line.name or "")
            elif line.product_id.odin_instalment_interest:
                continue
            else:
                if current is None:
                    current = start("Cover")
                # A cover line added by hand goes to the end of the order, so it
                # lands under whatever heading happens to precede it -- in
                # practice Statutory charges or Payment options, where its
                # premium silently inflates a section of fees and never gets a
                # row of its own. Neither of those is a benefit, so a line that
                # is not itself a charge belongs in its own row instead.
                target = current
                if current["key"] in self.NOT_A_BENEFIT and not self._is_charge(line):
                    if other is None:
                        other = start(_("Other benefits"), key="other_benefits")
                    target = other
                entry = {"line": line, "name": line.client_name}
                # Quantity decides, not the section's is_optional flag. A
                # section of add-ons stays flagged optional for the portal even
                # after the client picks one from it, and the line they picked
                # is charged on the order -- so it belongs in the cover table,
                # or the cover rows stop adding up to the total.
                if line.product_uom_qty:
                    target["included"].append(entry)
                    target["amount"] += line.price_subtotal
                else:
                    target["optional"].append(entry)

        return [s for s in sections if s["included"] or s["optional"] or s["notes"]]

    # -- details strip ----------------------------------------------------

    def _client_details(self):
        """Label/value pairs describing who and what is being quoted."""
        self.ensure_one()
        names = [name for name in DETAIL_FIELDS if name in self._fields]
        if not names:
            return []
        described = self.fields_get(names)
        details = []
        for name in names:
            value = self[name]
            if not value:
                continue
            labels = dict(described[name].get("selection") or [])
            details.append({
                "label": described[name]["string"],
                "value": str(labels.get(value, value)),
            })
        return details

    # -- notes ------------------------------------------------------------

    @api.model
    def _client_note_lines(self, note):
        """Split a note into bullets so it sets as a list, not a paragraph."""
        lines = [part.strip(" \t•-") for part in (note or "").splitlines()]
        return [line for line in lines if line]
