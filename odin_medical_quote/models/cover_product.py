"""Products: what the office sells, as staff keep it up to date.

A product is one insurer's cover -- *APA Jamii Plus – Family KES 1M*,
*Definite – Pick-up – Third party* -- and its rates, typed the way the
insurer's sheet prints them. Saved, it is quoted: New quote reads the same
records. Nobody edits quotation templates, product names or rate rows by
hand any more.

Each insurance line keeps the engine it has. Here, medical: a plan is a
quotation template whose priced lines come from rate tables. A rate table is
one table of the insurer's rate card (*Jamii Plus Outpatient – KES 100,000
per Person*): how it is priced, what it pays out, and one row per person,
age band or family size. Tables are shared: the seven Jamii Plus family
plans use the same outpatient tables, so a rate changed once is changed for
all of them, as it is on the card. Motor adds itself in odin_motor_quote.
"""

import re

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import formatLang

from .pricing import RateBook
from .product_template import RATE_BASIS, RATE_ROLE
from .rating import Household
from .sale_order_template import CHARGES

COVER_TYPES = [
    ("inpatient", "Inpatient"),
    ("outpatient", "Outpatient"),
    ("maternity", "Maternity"),
    ("dental", "Dental"),
    ("optical", "Optical"),
    ("other", "Other"),
]
#: rate-row fields that, changed, rename the row and are logged
ROW_FIELDS = ("odin_row_role", "odin_row_age_min", "odin_row_age_max", "odin_row_family_size")
#: a rate on a value that looks like the decimal point went missing
HIGH_RATE = 20.0


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


class CoverProduct(models.Model):
    _name = "odin.cover.product"
    _description = "Insurance product"
    _inherit = ["mail.thread"]
    _order = "line, insurer_id, sequence, name, id"

    name = fields.Char(required=True, tracking=True)
    line = fields.Selection(
        [("medical", "Medical")], string="Class", required=True, default="medical",
        tracking=True, help="The insurance line: it decides the questions New quote asks.")
    insurer_id = fields.Many2one(
        "res.partner", string="Insurer", required=True, index=True, tracking=True,
        domain=[("is_company", "=", True)])
    active = fields.Boolean(default=True, tracking=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    note = fields.Text(
        string="Notes on the quote",
        help="Printed with the quote: conditions, excess, what is included.")
    summary = fields.Char(string="What it covers", compute="_compute_summary")
    rate_count = fields.Integer(string="Rates", compute="_compute_rate_count")
    warning_html = fields.Html(string="Checks", compute="_compute_warning_html", sanitize=False)
    try_html = fields.Html(string="Result", compute="_compute_try_html", sanitize=False)

    # -- medical ------------------------------------------------------------
    template_id = fields.Many2one(
        "sale.order.template", string="Quotation template", copy=False, index=True,
        ondelete="set null", readonly=True,
        help="Made and kept up to date from this product: what New quote prices.")
    plan_type = fields.Selection(
        [("family", "A family"), ("child_only", "Children only")],
        string="Covers", default="family", tracking=True)
    terms = fields.Html(string="Terms on the quote")
    table_ids = fields.Many2many(
        "odin.cover.table", "odin_cover_product_table_rel", "product_id", "table_id",
        string="Covers and rates", copy=False)
    try_principal_age = fields.Integer(string="Principal's age", default=35)
    try_spouse_age = fields.Integer(string="Spouse's age", help="0: no spouse.")
    try_children = fields.Integer(string="Children")

    # -- reading ------------------------------------------------------------

    @api.depends(lambda self: self._odin_summary_depends())
    def _compute_summary(self):
        for product in self:
            product.summary = product._odin_summary()

    @api.model
    def _odin_summary_depends(self):
        return ["line", "plan_type", "table_ids.limit", "table_ids.cover"]

    def _odin_summary(self):
        self.ensure_one()
        if self.line != "medical":
            return ""
        core = self.table_ids.filtered(lambda t: t.cover == "inpatient")[:1]
        who = dict(self._fields["plan_type"]._description_selection(self.env)).get(
            self.plan_type, "")
        if core.limit:
            return "%s · %s" % (who, formatLang(self.env, core.limit, digits=0,
                                                  currency_obj=self.currency_id))
        return who

    @api.depends(lambda self: self._odin_rate_count_depends())
    def _compute_rate_count(self):
        for product in self:
            product.rate_count = product._odin_rate_count()

    @api.model
    def _odin_rate_count_depends(self):
        return ["line", "table_ids.row_ids"]

    def _odin_rate_count(self):
        self.ensure_one()
        return len(self.table_ids.row_ids) if self.line == "medical" else 0

    @api.depends(lambda self: self._odin_check_depends())
    def _compute_warning_html(self):
        for product in self:
            warnings = product._odin_warnings() if product.line else []
            product.warning_html = Markup("<ul class='mb-0'>%s</ul>") % Markup("").join(
                Markup("<li>%s</li>") % w for w in warnings) if warnings else False

    @api.model
    def _odin_check_depends(self):
        return ["line", "name", "insurer_id", "table_ids.row_ids.list_price",
                "table_ids.row_ids.odin_row_age_min", "table_ids.row_ids.odin_row_age_max",
                "table_ids.row_ids.odin_row_role", "table_ids.row_ids.odin_row_family_size",
                "table_ids.basis", "table_ids.cover"]

    def _odin_warnings(self):
        """What looks wrong, in words a broker reads at a glance. Warnings,
        not errors: a card can be odd and still right."""
        self.ensure_one()
        warnings = []
        others = self.search([
            ("id", "!=", self._origin.id or 0), ("name", "=", self.name),
            ("company_id", "=", self.company_id.id)]) if self.name else self.browse()
        if others:
            warnings.append(_("Another product is already called %s.", self.name))
        if self.line == "medical":
            warnings += self._odin_medical_warnings()
        return warnings

    def _odin_medical_warnings(self):
        warnings = []
        if not self.table_ids.filtered(lambda t: t.cover == "inpatient"):
            warnings.append(_("Add the inpatient cover: a plan cannot be quoted without it."))
        for table in self.table_ids:
            warnings += table._odin_warnings()
        return warnings

    @api.depends(lambda self: self._odin_try_depends())
    def _compute_try_html(self):
        for product in self:
            try:
                product.try_html = product._odin_try() if product.line else False
            except UserError as error:
                product.try_html = Markup("<p class='text-danger'>%s</p>") % error.args[0]

    @api.model
    def _odin_try_depends(self):
        return ["line", "template_id", "plan_type", "try_principal_age", "try_spouse_age",
                "try_children", "table_ids.row_ids.list_price", "table_ids.row_ids.odin_row_age_min",
                "table_ids.row_ids.odin_row_age_max", "table_ids.row_ids.odin_row_role",
                "table_ids.row_ids.odin_row_family_size"]

    def _odin_try(self):
        """The premium for the people typed in, priced by New quote's engine."""
        self.ensure_one()
        if self.line != "medical":
            return False
        template = self.template_id
        if not template or not self._origin.id:
            return Markup("<p class='text-muted'>%s</p>") % _(
                "Save the product, with its inpatient rates, to try it.")
        child_only = self.plan_type == "child_only"
        children = max(self.try_children, 0)
        household = Household(
            principal_age=None if child_only else max(self.try_principal_age, 0),
            spouse_age=None if child_only else (self.try_spouse_age or None),
            children=max(children, 1) if child_only else children)
        offer = template._odin_offer(household, RateBook(self.env, self.company_id))
        if not offer["available"]:
            return Markup("<p class='text-danger'>%s</p>") % offer["reason"]
        rows = [(line.who(), line.subtotal) for line in offer["core"]]
        premium = sum(amount for _who, amount in rows)
        levies = sum(self._odin_levies(line.product_id, line.subtotal) for line in offer["core"])
        charges = sum(line.subtotal for line in offer["charges"])
        rows += [(_("Levies"), levies), (_("Stamp duty and charges"), charges)]
        table = self._odin_try_table(rows, _("Total, inpatient only"), premium + levies + charges)
        options = []
        for group in offer["groups"]:
            for option in group["options"]:
                label = "%s – %s" % (group["label"], option["label"] or option["name"])
                options.append(Markup("<li>%s: %s</li>") % (
                    label, self._odin_money(option["amount"]) if option["available"]
                    else option["reason"]))
        if options:
            table += Markup("<p class='mt-2 mb-1'><b>%s</b></p><ul>%s</ul>") % (
                _("Optional covers for these people"), Markup("").join(options))
        return table

    def _odin_levies(self, product, amount):
        taxes = product.taxes_id.filtered(lambda t: t.company_id == self.company_id) \
            or product.taxes_id
        if not taxes:
            return 0.0
        result = taxes.compute_all(amount, currency=self.currency_id, product=product)
        return result["total_included"] - result["total_excluded"]

    def _odin_money(self, amount):
        return formatLang(self.env, amount, currency_obj=self.currency_id)

    def _odin_try_table(self, rows, total_label, total):
        body = Markup("").join(
            Markup("<tr><td>%s</td><td class='text-end'>%s</td></tr>") % (label, self._odin_money(amount))
            for label, amount in rows)
        return Markup(
            "<table class='table table-sm mb-0'><tbody>%s</tbody><tfoot><tr>"
            "<th>%s</th><th class='text-end'>%s</th></tr></tfoot></table>") % (
            body, total_label, self._odin_money(total))

    # -- writing ------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        if not self.env.context.get("odin_adopting"):
            products.filtered(lambda p: p.line == "medical")._odin_sync_template()
        return products

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get("odin_adopting"):
            self.filtered(lambda p: p.line == "medical")._odin_sync_template()
        return result

    def copy(self, default=None):
        """Duplicate: next year's card, or a plan like this one. A copied
        medical plan gets copies of its tables, so changing the copy never
        changes the original."""
        medical = self.filtered(lambda p: p.line == "medical" and p.template_id)
        new = super(CoverProduct, self - medical).copy(default)
        for product in medical:
            new |= product._odin_copy_medical(default)
        return new

    def copy_data(self, default=None):
        default = dict(default or {})
        vals_list = super().copy_data(default)
        for product, vals in zip(self, vals_list):
            if "name" not in default:
                vals["name"] = _("%s (copy)", product.name)
            if "active" not in default:
                # Off until edited and switched on: never quoted twice by accident.
                vals["active"] = False
        return vals_list

    def _odin_copy_medical(self, default=None):
        self.ensure_one()
        vals = self.with_context(odin_adopting=True).copy_data(default)[0]
        template = self.template_id.copy({"name": vals["name"], "active": vals.get("active", False)})
        variants = {}
        tables = self.env["odin.cover.table"]
        for table in self.table_ids:
            copy = table.copy({"name": _("%s (copy)", table.name)})
            tables |= copy
            for row in table.row_ids:
                new_row = row.copy({"odin_cover_table_id": copy.id})
                variants[row.product_variant_id.id] = new_row.product_variant_id.id
        for line in template.sale_order_template_line_ids:
            if line.product_id.id in variants:
                line.product_id = variants[line.product_id.id]
        vals.update(template_id=template.id, table_ids=[Command.set(tables.ids)])
        return self.with_context(odin_adopting=True).create(vals)

    def unlink(self):
        """A product that has been quoted is archived instead: its quotes,
        orders and invoices keep pointing at it."""
        quoted = self.filtered(lambda p: p._odin_quoted())
        if quoted:
            quoted.action_archive()
        return super(CoverProduct, self - quoted).unlink()

    def _odin_quoted(self):
        self.ensure_one()
        if self.line == "medical" and self.template_id:
            return bool(self.env["sale.order"].sudo().with_context(active_test=False).search_count(
                [("sale_order_template_id", "=", self.template_id.id)], limit=1))
        return False

    def action_archive(self):
        result = super().action_archive()
        self.filtered("template_id").template_id.active = False
        return result

    def action_unarchive(self):
        result = super().action_unarchive()
        self.filtered("template_id").template_id.active = True
        return result

    # -- the quotation template behind a medical plan -------------------------

    def _odin_sync_template(self):
        """Keep the plan's quotation template in step with the plan: its
        name, insurer, terms, and one priced line per rate table. Lines the
        template has beyond that -- benefit notes, charges, payment terms --
        are left as they are."""
        Template = self.env["sale.order.template"].sudo()
        for product in self:
            template = product.template_id
            if not template:
                if not product.table_ids.row_ids:
                    continue
                template = Template.create(product._odin_template_values())
                product.with_context(odin_adopting=True).template_id = template
                product._odin_template_base_lines(template)
            values = {}
            for key, value in product._odin_template_values().items():
                current = template[key]
                if isinstance(current, models.BaseModel):
                    current = current.id
                if current != value:
                    values[key] = value
            if values:
                template.write(values)
            product._odin_sync_template_lines(template)
            if template.odin_plan_type != product.plan_type:
                template.odin_plan_type = product.plan_type

    def _odin_template_values(self):
        self.ensure_one()
        values = {
            "name": self.name,
            "odin_insurer_id": self.insurer_id.id,
            "active": self.active,
        }
        if not self.template_id:
            # A template shared by every company (as installed) stays shared.
            values["company_id"] = self.company_id.id
        if self.terms or not self.template_id:
            values["note"] = self.terms or False
        return values

    def _odin_template_base_lines(self, template):
        """Statutory charges and payment options for a new plan, as the most
        recent plan of the company carries them."""
        reference = self.env["sale.order.template"].search([
            ("odin_medical", "=", True), ("id", "!=", template.id),
            ("company_id", "in", [False, self.company_id.id]),
        ], order="id desc", limit=1)
        lines = []
        if reference:
            copying = False
            for line in reference.sale_order_template_line_ids.sorted(lambda l: (l.sequence, l.id)):
                if line.display_type == "line_section":
                    copying = line._client_benefit_key() in CHARGES
                if copying:
                    lines.append({
                        "display_type": line.display_type,
                        "name": line.name,
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.product_uom_qty,
                        "benefit_key": line.benefit_key,
                        "benefit_label": line.benefit_label,
                    })
        if not lines:
            stamp = self.env["product.product"].search(
                [("name", "ilike", "stamp duty"), ("sale_ok", "=", True)], limit=1)
            lines.append({"display_type": "line_section", "name": _("Statutory charges"),
                          "benefit_key": "statutory_charges",
                          "benefit_label": _("Statutory charges")})
            if stamp:
                lines.append({"product_id": stamp.id, "product_uom_qty": 1})
        start = max(template.sale_order_template_line_ids.mapped("sequence") or [0]) + 1000
        template.sale_order_template_line_ids = [
            Command.create(dict(values, sequence=start + index * 10))
            for index, values in enumerate(lines)]

    def _odin_sync_template_lines(self, template):
        """One priced line per table, under its cover's heading; lines of
        tables taken off the plan removed. Tables without rates wait."""
        lines = template.sale_order_template_line_ids.sorted(lambda l: (l.sequence, l.id))
        wanted = {table.rate_group: table for table in self.table_ids if table.row_ids}
        present = {}
        for line in lines:
            group = line.product_id.odin_rate_group
            if line.product_id and group and not line.product_id.odin_instalment_interest \
                    and line.product_id.odin_cover_table_id:
                present.setdefault(group, self.env["sale.order.template.line"])
                present[group] |= line
        removed = self.env["sale.order.template.line"]
        for group, group_lines in present.items():
            if group not in wanted:
                removed |= group_lines
        if removed:
            removed.unlink()
            self._odin_drop_empty_sections(template)
        missing = [table for group, table in wanted.items() if group not in present]
        for table in sorted(missing, key=lambda t: (t.cover != "inpatient", t._odin_cover_order(),
                                                    t.sequence, t.id)):
            self._odin_add_table_line(template, table)

    def _odin_drop_empty_sections(self, template):
        """A cover heading left with no priced line goes, with its notes."""
        lines = template.sale_order_template_line_ids.sorted(lambda l: (l.sequence, l.id))
        doomed = self.env["sale.order.template.line"]
        section, members, priced = None, self.env["sale.order.template.line"], False
        for line in list(lines) + [None]:
            if line is None or line.display_type == "line_section":
                if section and not priced and section._client_benefit_key() not in CHARGES:
                    doomed |= section | members
                if line is None:
                    break
                section, members, priced = line, self.env["sale.order.template.line"], False
                continue
            members |= line
            if line.product_id:
                priced = True
        doomed.unlink()

    def _odin_add_table_line(self, template, table):
        """The table's first row under its cover's heading, with the table's
        benefits as a note; the heading made where the plan has none."""
        lines = list(template.sale_order_template_line_ids.sorted(lambda l: (l.sequence, l.id)))
        key = table._odin_benefit_key()
        product = table.row_ids[:1].product_variant_id
        new = [{
            "product_id": product.id,
            "product_uom_qty": 1 if table.cover == "inpatient" else 0,
        }]
        if table.benefits:
            new.append({"display_type": "line_note", "name": table.benefits, "product_uom_qty": 0})
        index = None
        for position, line in enumerate(lines):
            if line.display_type == "line_section" and line._client_benefit_key() == key:
                index = position + 1
                while index < len(lines) and lines[index].display_type != "line_section":
                    index += 1
                break
        if index is None:
            new.insert(0, table._odin_section_values())
            # Before the charges and payment terms, after the other covers.
            index = next((position for position, line in enumerate(lines)
                          if line.display_type == "line_section"
                          and line._client_benefit_key() in CHARGES), len(lines))
            if table.cover == "inpatient":
                index = 0
        records = template.sale_order_template_line_ids.create([
            dict(values, sale_order_template_id=template.id) for values in new])
        ordered = lines[:index] + list(records) + lines[index:]
        for sequence, line in enumerate(ordered, start=1):
            if line.sequence != sequence * 10:
                line.sequence = sequence * 10

    # -- taking over what is already there -------------------------------------

    @api.model
    def _odin_adopt_templates(self):
        """Every medical plan made before Products existed gets its product
        and tables, read from its template: nothing is recreated, renamed or
        repriced, so quotes priced before and after agree to the cent."""
        Template = self.env["sale.order.template"].with_context(active_test=False)
        templates = Template.search([("odin_medical", "=", True)])
        adopted = self.with_context(active_test=False).search(
            [("template_id", "in", templates.ids)]).template_id
        Self = self.with_context(odin_adopting=True, mail_create_nolog=True,
                                 tracking_disable=True)
        for template in templates - adopted:
            tables = self.env["odin.cover.table"]
            for block in template._odin_blocks():
                if block["kind"] == "charges":
                    continue
                for group in block["groups"]:
                    for _key, lines in template._odin_rate_groups(group):
                        tables |= self.env["odin.cover.table"]._odin_adopt(
                            lines[0].product_id, block, template)
            Self.create(self._odin_adopt_values(template, tables))
        return True

    @api.model
    def _odin_adopt_values(self, template, tables):
        insurer = template.odin_insurer_id or self._odin_insurer(template._odin_insurer_name())
        return {
            "name": template.name,
            "line": "medical",
            "insurer_id": insurer.id,
            "template_id": template.id,
            "plan_type": template.odin_plan_type or "family",
            "terms": template.note,
            "table_ids": [Command.set(tables.ids)],
            "active": template.active,
            "company_id": (template.company_id or self.env.company).id,
            "sequence": template.sequence,
        }

    @api.model
    def _odin_insurer(self, name):
        Partner = self.env["res.partner"]
        return Partner.search([("name", "=", name), ("is_company", "=", True)], limit=1) \
            or Partner.create({"name": name or _("Insurer"), "is_company": True})

    # -- the menu ---------------------------------------------------------------

    def action_odin_open_template(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order.template",
            "res_id": self.template_id.id,
            "views": [(False, "form")],
        }


class CoverTable(models.Model):
    """One table of an insurer's rate card: who and how it prices, and a row
    per age band, person or family size."""

    _name = "odin.cover.table"
    _description = "Rate table"
    _order = "sequence, id"

    name = fields.Char(
        required=True, help="As the rate card heads it, e.g. Jamii Plus Outpatient – KES 100,000.")
    rate_group = fields.Char(
        readonly=True, copy=False, index=True,
        help="How New quote finds this table's rows. Set once, when the table is made.")
    sequence = fields.Integer(default=10)
    cover = fields.Selection(COVER_TYPES, required=True, default="inpatient")
    cover_label = fields.Char(
        string="Cover name", help="For another kind of cover: what the quote calls it.")
    basis = fields.Selection(RATE_BASIS, string="Priced", required=True, default="person")
    limit = fields.Monetary(string="Cover limit", currency_field="currency_id")
    requires_outpatient = fields.Boolean(
        string="Only with outpatient",
        help="Some insurers sell dental and optical only to clients taking outpatient cover.")
    section_title = fields.Char(
        string="Heading on the quote",
        help="E.g. Optional – dental cover (per person). Left empty, made from the cover.")
    benefits = fields.Text(string="Benefits printed under it")
    row_ids = fields.One2many("product.template", "odin_cover_table_id", string="Rates")
    product_ids = fields.Many2many(
        "odin.cover.product", "odin_cover_product_table_rel", "table_id", "product_id",
        string="Used by", copy=False)
    used_count = fields.Integer(compute="_compute_used_count")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    new_age_min = fields.Integer(
        string="Rows start at age", help="Pre-filled on a new row: the age band it covers.")
    new_age_max = fields.Integer(string="Rows end at age")

    _rate_group_unique = models.Constraint(
        "UNIQUE (rate_group, company_id)",
        "Two rate tables cannot share a rate group.")

    @api.depends("product_ids")
    def _compute_used_count(self):
        for table in self:
            table.used_count = len(table.product_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("rate_group"):
                vals["rate_group"] = self._odin_free_group(vals.get("name") or _("Rates"),
                                                           vals.get("company_id"))
        return super().create(vals_list)

    def write(self, vals):
        result = super().write(vals)
        if {"name", "basis", "limit"} & set(vals):
            self.row_ids._odin_rename_rows()
        if not self.env.context.get("odin_adopting"):
            self.product_ids._odin_sync_template()
        return result

    @api.model
    def _odin_free_group(self, name, company_id=None):
        company_id = company_id or self.env.company.id
        candidate, count = name.strip(), 1
        while self.with_context(active_test=False).search_count(
                [("rate_group", "=", candidate), ("company_id", "=", company_id)]):
            count += 1
            candidate = "%s (%s)" % (name.strip(), count)
        return candidate

    def _odin_benefit_key(self):
        self.ensure_one()
        if self.cover == "other":
            return slug(self.cover_label) or "other"
        return self.cover

    def _odin_cover_name(self):
        self.ensure_one()
        if self.cover == "other":
            return self.cover_label or _("Other cover")
        return dict(self._fields["cover"]._description_selection(self.env))[self.cover]

    def _odin_cover_order(self):
        return [key for key, _label in COVER_TYPES].index(self.cover)

    def _odin_section_values(self):
        self.ensure_one()
        label = self._odin_cover_name()
        if self.cover == "inpatient":
            title = self.section_title or (
                _("Inpatient – %s", formatLang(self.env, self.limit, digits=0,
                                               currency_obj=self.currency_id))
                if self.limit else _("Inpatient"))
        else:
            title = self.section_title or _("Optional – %s cover", label.lower())
            if self.requires_outpatient and "with outpatient" not in title.lower():
                title = _("%s (with outpatient cover)", title)
        return {
            "display_type": "line_section",
            "name": title,
            "benefit_key": self._odin_benefit_key(),
            "benefit_label": label,
            "cover_limit": self.limit if self.cover == "inpatient" else 0.0,
            "is_optional": self.cover != "inpatient",
            "product_uom_qty": 0,
        }

    def _odin_row_defaults(self):
        """What a new row inherits: the category and levies of the table's
        other rows, else of the most recent rate on the company's catalogue."""
        self.ensure_one()
        model = self.row_ids.with_context(active_test=False)[:1] or self.env["product.template"].search([
            ("odin_rate_basis", "in", ["person", "family_size"]),
            ("sale_ok", "=", True),
            ("company_id", "in", [False, self.company_id.id]),
        ], order="id desc", limit=1)
        values = {
            "type": "service",
            "sale_ok": True,
            "purchase_ok": False,
            "invoice_policy": "order",
        }
        if model:
            values.update(categ_id=model.categ_id.id, taxes_id=[Command.set(model.taxes_id.ids)])
        return values

    def _odin_warnings(self):
        self.ensure_one()
        warnings = []
        rows = self.row_ids
        if not rows:
            return [_("%s has no rates yet.", self.name)]
        if any(not row.list_price for row in rows):
            warnings.append(_("%s has a row with no premium.", self.name))
        if self.basis == "family_size":
            sizes = sorted(set(rows.mapped("odin_row_family_size")))
            if sizes and 0 in sizes:
                warnings.append(_("%s: say how many people each row covers.", self.name))
            missing = [size for size in range(1, (max(sizes) if sizes else 0) + 1)
                       if size not in sizes]
            if missing:
                warnings.append(_("%(table)s: no premium for a family of %(sizes)s.",
                                  table=self.name, sizes=", ".join(map(str, missing))))
        if self.basis == "person":
            for role in set(rows.mapped("odin_row_role")):
                bands = sorted((r.odin_row_age_min, r.odin_row_age_max or 999)
                               for r in rows.filtered(lambda r, role=role: r.odin_row_role == role))
                for (low, high), (next_low, _next_high) in zip(bands, bands[1:]):
                    who = dict(RATE_ROLE).get(role, role)
                    if next_low > high + 1:
                        warnings.append(_("%(table)s: no rate for %(who)s aged %(low)s to %(high)s.",
                                          table=self.name, who=who.lower(), low=high + 1,
                                          high=next_low - 1))
                    elif next_low <= high:
                        warnings.append(_("%(table)s: two rates for %(who)s aged %(age)s.",
                                          table=self.name, who=who.lower(), age=next_low))
        return warnings

    @api.model
    def _odin_adopt(self, product, block, template):
        """The table ``product``'s rate group is, made from the rows the
        catalogue already has."""
        group = product.odin_rate_group or product.name
        company = template.company_id or self.env.company
        table = self.with_context(active_test=False).search(
            [("rate_group", "=", group), ("company_id", "=", company.id)], limit=1)
        if table:
            return table
        key = block["key"]
        cover = key if key in dict(COVER_TYPES) else "other"
        table = self.create({
            "name": group,
            "rate_group": group,
            "cover": cover,
            "cover_label": block["label"] if cover == "other" else False,
            "basis": product.odin_rate_basis or "policy",
            "limit": product.odin_rate_limit,
            "requires_outpatient": block["requires"] == "outpatient",
            "section_title": block["section"].name if block["section"] else False,
            "company_id": company.id,
        })
        siblings = RateBook(self.env, company).siblings(product)
        for row in siblings.product_tmpl_id:
            row.with_context(odin_no_rename=True).write({
                "odin_cover_table_id": table.id,
                "odin_row_role": row.odin_rate_role or "any",
                "odin_row_age_min": row.odin_rate_age_min,
                "odin_row_age_max": row.odin_rate_age_max,
                "odin_row_family_size": row.odin_rate_family_size,
            })
        return table



class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.model_create_multi
    def create(self, vals_list):
        Table = self.env["odin.cover.table"]
        for vals in vals_list:
            table = Table.browse(vals.get("odin_cover_table_id") or [])
            if table:
                for key, value in table._odin_row_defaults().items():
                    vals.setdefault(key, value)
                vals.setdefault("name", table.name)
                if table.new_age_min and "odin_row_age_min" not in vals:
                    vals["odin_row_age_min"] = table.new_age_min
                if table.new_age_max and "odin_row_age_max" not in vals:
                    vals["odin_row_age_max"] = table.new_age_max
        templates = super().create(vals_list)
        rows = templates.filtered("odin_cover_table_id")
        if rows and not self.env.context.get("odin_no_rename"):
            rows._odin_rename_rows()
            for row in rows:
                row._odin_log_rows(_("Rate added: %s", row._odin_row_text()))
            rows.odin_cover_table_id.product_ids._odin_sync_template()
        return templates

    def write(self, vals):
        before = {}
        if "list_price" in vals or set(ROW_FIELDS) & set(vals):
            before = {row.id: row._odin_row_text() for row in self.filtered("odin_cover_table_id")}
        result = super().write(vals)
        rows = self.filtered("odin_cover_table_id")
        if rows and set(ROW_FIELDS + ("odin_cover_table_id",)) & set(vals) \
                and not self.env.context.get("odin_no_rename"):
            rows._odin_rename_rows()
        for row in rows.filtered(lambda r: r.id in before):
            after = row._odin_row_text()
            if after != before[row.id]:
                row._odin_log_rows(_("Rate changed: %(old)s → %(new)s",
                                     old=before[row.id], new=after))
        return result

    def unlink(self):
        """A rate row is archived, never deleted: quotations and invoices
        made from it keep pointing at it, and New quote stops using it."""
        rows = self.filtered("odin_cover_table_id")
        for row in rows:
            row._odin_log_rows(_("Rate removed: %s", row._odin_row_text()))
        if rows:
            rows.write({"active": False})
        return super(ProductTemplate, self - rows).unlink()

    def _odin_row_text(self):
        self.ensure_one()
        return "%s: %s" % (self._odin_row_label(), formatLang(
            self.env, self.list_price, currency_obj=self.currency_id))

    def _odin_row_label(self):
        """``Principal, 21–40`` / ``Member + 2`` / ``Per policy``."""
        self.ensure_one()
        table = self.odin_cover_table_id
        if table.basis == "family_size":
            size = self.odin_row_family_size
            label = _("Member only") if size == 1 else _("Member + %s", max(size - 1, 0))
        elif table.basis == "person":
            label = dict(RATE_ROLE).get(self.odin_row_role or "any", "")
        else:
            label = _("Per policy")
        low, high = self.odin_row_age_min, self.odin_row_age_max
        if low or high:
            ages = ("%s–%s" % (low, high)) if high else _("%s and over", low)
            label = "%s, %s" % (label, _("aged %s", ages))
        return label

    def _odin_rename_rows(self):
        """``Jamii Plus Outpatient – KES 100,000 – Principal, aged 21–40``:
        what the quotation line says. Rows are priced from their fields, so
        the name is for people only."""
        for row in self.filtered("odin_cover_table_id"):
            name = "%s – %s" % (row.odin_cover_table_id.name, row._odin_row_label())
            if row.name != name:
                super(ProductTemplate, row.with_context(odin_no_rename=True)).write({"name": name})

    def _odin_log_rows(self, body):
        for row in self:
            for product in row.odin_cover_table_id.product_ids:
                product.message_post(body=Markup("%s <i>(%s)</i>") % (
                    body, row.odin_cover_table_id.name))
