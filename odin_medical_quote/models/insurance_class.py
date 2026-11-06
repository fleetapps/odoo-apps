"""Insurance classes: what the office quotes -- Medical, Motor, Accident...

A class is the first thing New quote asks, what a product is filed under,
and what a quote and a renewal are of. Medical and Motor come with screens
of their own, which know families and vehicles. Any class the office adds
itself -- Accident, Fire, Travel, WIBA -- is quoted on the standard screen:
the client, the amount insured and the people covered if the class asks for
them, then every insurer's product for the class priced side by side, with
its extras, and saved, compared, printed, accepted and invoiced like any
other quote.

Quotes, products and renewals refer to a class by its code, which is made
from the name once and never changes: renaming Accident to Personal
Accident renames it everywhere, and nothing already quoted moves.
"""

import re

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

#: what a class's button shows in New quote (Font Awesome 4)
ICONS = [
    ("fa-shield", "Shield"),
    ("fa-heartbeat", "Heartbeat"),
    ("fa-car", "Car"),
    ("fa-ambulance", "Ambulance"),
    ("fa-medkit", "First aid kit"),
    ("fa-user", "Person"),
    ("fa-users", "Group of people"),
    ("fa-home", "Home"),
    ("fa-building", "Building"),
    ("fa-fire", "Fire"),
    ("fa-plane", "Plane"),
    ("fa-ship", "Ship"),
    ("fa-truck", "Truck"),
    ("fa-briefcase", "Briefcase"),
    ("fa-gavel", "Gavel"),
    ("fa-money", "Money"),
    ("fa-graduation-cap", "Graduation cap"),
    ("fa-leaf", "Leaf"),
    ("fa-paw", "Paw"),
    ("fa-umbrella", "Umbrella"),
]


def class_code(name):
    """``Personal Accident`` -> ``personal_accident``."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_") or "class"


class InsuranceClass(models.Model):
    _name = "odin.insurance.class"
    _description = "Insurance class"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        readonly=True, copy=False, index=True,
        help="How quotes, products and renewals refer to the class. Made from the "
        "name when the class is created and never changed, so a rename moves nothing.")
    engine = fields.Selection(
        [("medical", "The medical quote screen"), ("general", "The standard quote screen")],
        string="Quoted on", required=True, default="general", readonly=True,
        help="Medical and Motor have screens of their own. A class you add is quoted "
        "on the standard screen, with the questions set below.")
    description = fields.Char(
        translate=True,
        help="Under the class's name in New quote, e.g. Personal accident cover for "
        "individuals and groups.")
    icon = fields.Selection(ICONS, default="fa-shield", required=True)
    sequence = fields.Integer()
    active = fields.Boolean(default=True)

    # -- what the standard screen asks ----------------------------------------
    ask_amount = fields.Boolean(
        string="Ask for an amount", default=True,
        help="The amount the premium is worked out on: the sum insured, the annual "
        "wages, the limit of indemnity... Products of the class have bands of it.")
    amount_label = fields.Char(
        string="Amount called", translate=True, default="Sum insured",
        help="What the quote calls the amount, e.g. Sum insured, Annual wages, "
        "Limit of indemnity.")
    ask_people = fields.Boolean(
        string="Ask how many people",
        help="For cover priced per person: personal accident, travel, group life. "
        "A product says whether its premium is per person.")
    people_label = fields.Char(string="People called", translate=True, default="People covered")
    details_label = fields.Char(
        string="Details called", translate=True, default="What is insured",
        help="One line of free text the quote asks for and prints, e.g. Occupation, "
        "Destination, Address of the property.")
    product_id = fields.Many2one(
        "product.product", string="Invoiced as", ondelete="restrict",
        domain=[("type", "=", "service")],
        help="The product the premium and extras of this class go on, on quotations "
        "and invoices. Made for you with the levies of your other premiums; set its "
        "taxes in Sales ▸ Products if this class's differ.")
    product_count = fields.Integer(string="Products", compute="_compute_counts")
    quote_count = fields.Integer(string="Quotes", compute="_compute_counts")

    _code_unique = models.Constraint("UNIQUE (code)", "Two insurance classes cannot share a code.")

    # -- what the rest of the app reads ---------------------------------------

    @api.model
    def _odin_selection(self):
        """The classes as the Class of a product, quote or renewal offers
        them: the active ones, and an archived one only while something is
        still filed under it, so what was quoted keeps its name."""
        classes = self.sudo().with_context(active_test=False).search([])
        archived = classes.filtered(lambda c: not c.active)
        in_use = archived._odin_in_use() if archived else set()
        return [(c.code, c.name if c.active else _("%s (archived)", c.name))
                for c in classes if c.active or c.code in in_use]

    def _odin_in_use(self):
        """The codes of ``self`` something is filed under."""
        codes = set(self.mapped("code"))
        used = set()
        for model, field in (("odin.cover.product", "line"),
                             ("odin.quote.comparison", "odin_quote_type"),
                             ("odin.renewal", "line")):
            Model = self.env[model].sudo().with_context(active_test=False)
            for row in Model._read_group([(field, "in", list(codes))], [field]):
                used.add(row[0])
        return used

    @api.model
    def _odin_get(self, code):
        """The class of ``code``, archived or not; Medical for an empty one,
        as quotes made before classes existed were."""
        return self.with_context(active_test=False).search(
            [("code", "=", code or "medical")], limit=1)

    @api.model
    def _odin_engine_tags(self):
        """The screen (client action) each engine quotes on; motor adds its own."""
        return {"medical": "odin_medical_quote", "general": "odin_general_quote"}

    def _odin_builder_name(self):
        self.ensure_one()
        return _("Add %s Quote", self.name)

    def _odin_builder_action(self, **params):
        """The screen to quote this class on, with ``params`` (a lead, a quote
        to edit or renew)."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": self._odin_engine_tags().get(self.engine, "odin_general_quote"),
            "name": self._odin_builder_name(),
            "params": dict(params, class_id=self.id),
        }

    @api.model
    def _odin_builder_tags(self):
        """``{code: (screen, name)}`` for every class."""
        tags = self._odin_engine_tags()
        return {c.code: (tags.get(c.engine, "odin_general_quote"), c._odin_builder_name())
                for c in self.with_context(active_test=False).search([])}

    @api.model
    def odin_quote_types(self):
        """New quote's buttons and the Quotes list's New menu: each active
        class, with the screen it opens. A standard class without products
        has nothing to quote yet, and says so."""
        counts = dict(self.env["odin.cover.product"]._read_group(
            [("company_id", "=", self.env.company.id)], ["line"], ["__count"]))
        return [{
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "description": c.description or "",
            "icon": c.icon or "fa-shield",
            "engine": c.engine,
            "products": counts.get(c.code, 0),
            "available": c.engine != "general" or bool(counts.get(c.code)),
            "action": c._odin_builder_action(),
        } for c in self.search([])]

    def _odin_premium_product(self):
        """What this class's premium is invoiced as: made the first time it
        is needed, a service with the levies of the company's other premiums."""
        self.ensure_one()
        if self.product_id:
            return self.product_id
        model = self.env["product.template"].sudo().search([
            ("odin_rate_basis", "in", ["person", "family_size"]),
            ("sale_ok", "=", True), ("taxes_id", "!=", False),
            ("company_id", "in", [False, self.env.company.id]),
        ], order="id desc", limit=1)
        values = {
            "name": _("%s premium", self.name),
            "type": "service",
            "sale_ok": True,
            "purchase_ok": False,
            "invoice_policy": "order",
            "list_price": 0.0,
            "taxes_id": [Command.set(model.taxes_id.ids)],
        }
        if model:
            values["categ_id"] = model.categ_id.id
        product = self.env["product.product"].sudo().create(values)
        self.sudo().product_id = product
        return product

    @api.depends("code")
    def _compute_counts(self):
        products = dict(self.env["odin.cover.product"].with_context(active_test=False)._read_group(
            [("line", "in", self.mapped("code"))], ["line"], ["__count"]))
        quotes = dict(self.env["odin.quote.comparison"]._read_group(
            [("odin_quote_type", "in", self.mapped("code"))], ["odin_quote_type"], ["__count"]))
        for record in self:
            record.product_count = products.get(record.code, 0)
            record.quote_count = quotes.get(record.code, 0)

    # -- writing --------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        existing = self.with_context(active_test=False).search([])
        taken = set(existing.mapped("code"))
        last = max(existing.mapped("sequence") or [0])
        for vals in vals_list:
            if "sequence" not in vals:
                # A class added goes after those already in New quote.
                last += 10
                vals["sequence"] = last
            code = vals.get("code") or class_code(vals.get("name"))
            base, count = code, 1
            while code in taken:
                count += 1
                code = "%s_%s" % (base, count)
            vals["code"] = code
            taken.add(code)
        classes = super().create(vals_list)
        for record in classes.filtered(lambda c: c.engine == "general" and not c.product_id):
            record._odin_premium_product()
        return classes

    def write(self, vals):
        for key in ("code", "engine"):
            if key in vals and any(record[key] != vals[key] for record in self):
                raise UserError(_(
                    "The screen a class is quoted on, and how quotes refer to it, cannot "
                    "change once it exists. Add a new class instead."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_used(self):
        for record in self:
            if record.engine != "general":
                raise UserError(_(
                    "%s comes with its own quote screen and cannot be deleted. Archive it "
                    "to take it out of New quote.", record.name))
        used = self._odin_in_use()
        if used:
            names = ", ".join(self.filtered(lambda c: c.code in used).mapped("name"))
            raise UserError(_(
                "%s has products or quotes filed under it. Archive it instead: it leaves "
                "New quote and the products, and what was quoted keeps its name.", names))

    # -- buttons --------------------------------------------------------------

    def action_odin_products(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "odin_medical_quote.action_cover_product")
        action["domain"] = [("line", "=", self.code)]
        action["context"] = {"default_line": self.code}
        action["name"] = _("%s products", self.name)
        return action

    def action_odin_quotes(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "odin_client_quote.action_quote_comparison")
        action["domain"] = [("odin_quote_type", "=", self.code)]
        action["context"] = {"create": False}
        action["name"] = _("%s quotes", self.name)
        return action
