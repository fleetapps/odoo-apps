import math
from datetime import datetime, time

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero

# Reason -> (direction, label, what it posts as, reference prefix)
REASONS = {
    "filling": ("in", "Bottle filling", "Manufacturing order (filling)", "FIL"),
    "production": ("in", "Production batch", "Manufacturing order (batch)", "PRD"),
    "received": ("in", "Received", "Purchase order and receipt", "IN"),
    "return": ("in", "Customer return", "Return from customer", "RET"),
    "sale": ("out", "Sale", "Delivery to customer", "OUT"),
    "spoiled": ("out", "Spoiled", "Scrap", "SCR"),
    "sample": ("out", "Sample / gift", "Scrap (sample or gift)", "SMP"),
    "used": ("out", "Used in-house", "Scrap (used in-house)", "USE"),
    "correction": ("both", "Correction", "Inventory adjustment", "ADJ"),
}
REASON_SELECTION = [(key, value[1]) for key, value in REASONS.items()]
NEEDS_PARTNER = {"sale", "return", "received"}
MAKES = {"filling", "production"}
SEQUENCE_CODE = "odin.stock.line.%s"

# A posted line is undone by the line that does the opposite, so the
# history stays: what each reason reverses into.
REVERSE = {"sale": "return", "return": "sale", "spoiled": "correction", "sample": "correction",
           "used": "correction", "correction": "correction"}

# Written by posting and cancelling only: not by the form, an import or a
# call from outside, which would turn a posted line back into a draft, or
# mark a draft posted without its stock moves.
SYSTEM_FIELDS = {"state", "ref", "onhand_after", "posted_by_id", "posted_on", "production_id", "picking_id",
                 "scrap_id", "move_ids", "partner_id", "purchase_line_id", "stock_purchase_id"}
# What a posted (or cancelled) line keeps as it was.
LOCKED_FIELDS = {"date", "reason", "product_id", "qty_in", "qty_out", "bom_id", "component_ids", "warehouse_id",
                 "partner_name", "unit_cost", "company_id", "reverses_id", "purchase_ref", "tax_id"}

STATUS = {
    "need_product": ("Pick the item", "warning"),
    "need_reason": ("Pick a reason", "warning"),
    "need_qty": ("Type how many", "warning"),
    "wrong_way": ("Added or removed?", "warning"),
    "tracked": ("Needs lot numbers", "danger"),
    "need_recipe": ("No recipe", "danger"),
    "need_partner": ("Which customer?", "warning"),
    "need_supplier": ("Which supplier?", "warning"),
    "need_cost": ("Cost per unit?", "warning"),
    "future": ("Dated ahead", "warning"),
    "short": ("Short", "danger"),
    "ready": ("Ready to post", "primary"),
    "posted": ("Posted", "success"),
    "cancelled": ("Cancelled", "muted"),
}
NEED_KEYS = {"need_product", "need_reason", "need_qty", "wrong_way", "tracked", "need_recipe", "need_partner",
             "need_supplier", "need_cost", "future"}


def _normal_name(name):
    return " ".join((name or "").split()).upper()


def _normal_ref(ref):
    return " ".join((ref or "").split())


def _like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class StockBookLine(models.Model):
    """One line of the movement log."""

    _name = "odin.stock.line"
    _description = "Stock Book movement"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"
    _rec_name = "ref"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one(
        "stock.warehouse", required=True, check_company=True,
        default=lambda self: self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1))
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    ref = fields.Char(string="Reference", readonly=True, copy=False, index=True)
    reason = fields.Selection(REASON_SELECTION, tracking=True)
    product_id = fields.Many2one("product.product", string="Item", check_company=True, tracking=True,
                                 domain=[("is_storable", "=", True)])
    uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    qty_in = fields.Float(string="Added", digits="Product Unit", tracking=True)
    qty_out = fields.Float(string="Removed", digits="Product Unit", tracking=True)
    partner_name = fields.Char(string="Customer / supplier", tracking=True)
    partner_id = fields.Many2one("res.partner", readonly=True, copy=False)
    note = fields.Char()
    unit_cost = fields.Float(digits="Product Price", help="Received goods: cost per unit, as the VAT on the line "
                             "reads it (before VAT unless that tax is included in the price). It becomes the price "
                             "on the purchase order, and the receipt is valued at it without the VAT.")
    purchase_ref = fields.Char(string="LPO / invoice no.", copy=False, index="btree_not_null",
                               help="The number on the LPO or the supplier's invoice: the purchase order's Vendor "
                               "Reference. Lines with the same number and supplier are one purchase order.")
    tax_id = fields.Many2one("account.tax", string="VAT", copy=False, check_company=True,
                             domain=[("type_tax_use", "=", "purchase")],
                             help="Empty: the VAT this supplier and item usually have, added on top of the cost.")
    purchase_line_id = fields.Many2one("purchase.order.line", readonly=True, copy=False, index="btree_not_null",
                                       string="Purchase order line")
    purchase_id = fields.Many2one(related="purchase_line_id.order_id", string="Purchase order")
    stock_purchase_id = fields.Many2one("odin.stock.purchase", string="Purchase window entry", readonly=True, copy=False,
                                        index="btree_not_null", ondelete="restrict")
    bill_id = fields.Many2one(related="stock_purchase_id.bill_id", string="Vendor bill")
    bom_id = fields.Many2one("mrp.bom", string="Recipe", check_company=True)
    component_ids = fields.One2many("odin.stock.line.component", "line_id", copy=True)
    state = fields.Selection([("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")],
                             default="draft", required=True, readonly=True, copy=False, tracking=True)
    onhand_after = fields.Float(digits="Product Unit", readonly=True, copy=False,
                                help="On hand in the warehouse right after this line was posted.")
    posted_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    posted_on = fields.Datetime(readonly=True, copy=False)
    production_id = fields.Many2one("mrp.production", readonly=True, copy=False)
    picking_id = fields.Many2one("stock.picking", readonly=True, copy=False)
    scrap_id = fields.Many2one("stock.scrap", readonly=True, copy=False)
    move_ids = fields.Many2many("stock.move", readonly=True, copy=False)
    reverses_id = fields.Many2one("odin.stock.line", string="Reverses", readonly=True, copy=False, index="btree_not_null",
                                  help="The posted line this one undoes.")

    _ref_unique = models.UniqueIndex("(ref) WHERE ref IS NOT NULL", "Another movement already has this reference.")

    # ------------------------------------------------------------ basics
    @property
    def direction(self):
        return REASONS.get(self.reason, ("both",))[0]

    @property
    def qty(self):
        return self.qty_in or self.qty_out

    def _location(self):
        return self.warehouse_id.lot_stock_id

    @api.model
    def _available(self, products, warehouse):
        """On hand in the warehouse's stock (and its sub-locations)."""
        if not products or not warehouse:
            return dict.fromkeys(products.ids, 0.0)
        quants = self.env["stock.quant"]._read_group(
            [("product_id", "in", products.ids), ("location_id", "child_of", warehouse.lot_stock_id.id)],
            ["product_id"], ["quantity:sum"])
        found = {product.id: qty for product, qty in quants}
        return {p.id: found.get(p.id, 0.0) for p in products}

    def _recipes(self):
        self.ensure_one()
        if not self.product_id:
            return self.env["mrp.bom"]
        return self.env["mrp.bom"].search([
            "|", ("product_id", "=", self.product_id.id),
            "&", ("product_id", "=", False), ("product_tmpl_id", "=", self.product_id.product_tmpl_id.id),
            ("type", "=", "normal"), "|", ("company_id", "=", False), ("company_id", "=", self.company_id.id),
        ], order="sequence, id")

    @api.model
    def _round_component(self, qty, uom):
        """Whole pieces for things counted in units (cartons, bottles,
        labels); the unit's own rounding for liquids and powders."""
        if uom and uom == self.env.ref("uom.product_uom_unit", raise_if_not_found=False):
            return float(math.ceil(round(qty, 6)))
        return uom.round(qty) if uom else round(qty, 2)

    def _explode(self):
        """Fill the components from the recipe for the quantity made, the
        way the manufacturing order will: kits are opened up into what they
        contain and recipe lines for other variants are left out, so the
        line and the order it posts agree."""
        for line in self.filtered(lambda l: l.state == "draft").with_context(odin_stock_exploding=True):
            if line.reason not in MAKES or not line.product_id or not line.qty_in:
                line.component_ids = [Command.clear()]
                continue
            bom = line.bom_id if line.bom_id in line._recipes() else line._recipes()[:1]
            line.bom_id = bom
            if not bom:
                line.component_ids = [Command.clear()]
                continue
            factor = line.product_id.uom_id._compute_quantity(line.qty_in, bom.product_uom_id, round=False) \
                / bom.product_qty
            _boms, bom_lines = bom.explode(line.product_id, factor, picking_type=bom.picking_type_id)
            commands = [Command.clear()]
            for seq, (bom_line, data) in enumerate(bom_lines):
                product = bom_line.product_id
                if product.type != "consu":
                    continue
                qty = bom_line.product_uom_id._compute_quantity(data["qty"], product.uom_id, round=False)
                commands.append(Command.create({
                    "sequence": seq, "product_id": product.id,
                    "qty": line._round_component(qty, product.uom_id), "from_recipe": True,
                }))
            line.component_ids = commands

    def _place_qty(self):
        """A reason that only adds (or only removes) takes the quantity in its
        own column: 24 typed in Added for a Sale is 24 removed."""
        moved = self.env["odin.stock.line"]
        if self.env.context.get("odin_stock_keep_column"):
            # Undone from the page: the quantity stays where it was typed.
            return moved
        for line in self.filtered(lambda l: l.state == "draft"):
            if line.direction == "out" and line.qty_in and not line.qty_out:
                super(StockBookLine, line).write({"qty_out": line.qty_in, "qty_in": 0.0})
                moved |= line
            elif line.direction == "in" and line.qty_out and not line.qty_in:
                super(StockBookLine, line).write({"qty_in": line.qty_out, "qty_out": 0.0})
                moved |= line
        return moved

    def _unlimited(self, product):
        """Never short: things nobody counts (ticked on the product) and
        consumables Odoo does not keep stock of."""
        return product.stock_book_unlimited or not product.is_storable

    # ------------------------------------------------------------ status
    def _status(self, available=None):
        """(key, label, tone, detail) for the book. ``available``: on hand by
        product id, as projected by the book for drafts."""
        self.ensure_one()
        if self.state != "draft":
            label, tone = STATUS[self.state]
            return self.state, label, tone, ""
        if not self.product_id:
            return "need_product", *STATUS["need_product"], ""
        tracked = (self.product_id | self.component_ids.product_id).filtered(lambda p: p.tracking != "none")
        if tracked:
            return "tracked", *STATUS["tracked"], _(
                "%s is tracked by lot or serial number. Post it from Inventory or Manufacturing, where the "
                "numbers are entered.", ", ".join(tracked.mapped("display_name")))
        if not self.reason:
            return "need_reason", *STATUS["need_reason"], ""
        if float_is_zero(self.qty, precision_digits=4) or (self.qty_in and self.qty_out):
            return "need_qty", *STATUS["need_qty"], _("Type a number in Added or Removed, not both.")
        if (self.direction == "in" and self.qty_out) or (self.direction == "out" and self.qty_in):
            word = _("Added") if self.direction == "in" else _("Removed")
            return "wrong_way", *STATUS["wrong_way"], _("%(reason)s goes in %(column)s.",
                                                         reason=REASONS[self.reason][1], column=word)
        if self.reason in MAKES and not self.bom_id:
            return "need_recipe", *STATUS["need_recipe"], _("%s has no recipe (Bill of Materials).",
                                                            self.product_id.display_name)
        if self.reason in NEEDS_PARTNER and not (self.partner_name or "").strip():
            if self.reason == "received":
                return "need_supplier", *STATUS["need_supplier"], ""
            return "need_partner", *STATUS["need_partner"], ""
        if self.reason == "received" and float_compare(self.unit_cost, 0.0, precision_digits=4) <= 0:
            return "need_cost", *STATUS["need_cost"], _("Type what one %(unit)s cost%(vat)s.",
                                                        unit=self.product_id.uom_id.name,
                                                        vat="" if self.tax_id else _(", before VAT"))
        if self.date > fields.Date.context_today(self):
            return "future", *STATUS["future"], _("Dated %s: it can be posted on the day.",
                                                  self.date.strftime("%d %b %Y"))
        available = available if available is not None else self._available(
            self.product_id | self.component_ids.product_id, self.warehouse_id)
        short = []
        if self.qty_out and float_compare(self.qty_out, available.get(self.product_id.id, 0.0),
                                          precision_digits=4) > 0:
            short.append(self.product_id)
        for comp in self.component_ids:
            if self._unlimited(comp.product_id):
                continue
            if float_compare(comp.qty, available.get(comp.product_id.id, 0.0), precision_digits=4) > 0:
                short.append(comp.product_id)
        if short:
            # The count fits the status column; the names are in the detail.
            label = _("Short · 1 item") if len(short) == 1 else _("Short · %s items", len(short))
            return "short", label, "danger", _(
                "More than is on hand: %s. Count them, receive the delivery, or change the quantity.",
                ", ".join(p.name for p in short))
        return "ready", *STATUS["ready"], ""

    # ------------------------------------------------------------ create / write
    def _check_system_fields(self, vals, creating=False):
        if self.env.context.get("odin_stock_book_posting"):
            return
        # A new line may say it is a draft; nothing else is set by hand.
        touched = {key for key in SYSTEM_FIELDS & vals.keys()
                   if not (creating and key == "state" and vals[key] == "draft")}
        if touched:
            raise UserError(_("Posting, cancelling and references are done by the Stock Book: post or cancel "
                              "the line instead of changing %s.", ", ".join(sorted(touched))))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_system_fields(vals, creating=True)
            if vals.get("partner_name"):
                vals["partner_name"] = _normal_name(vals["partner_name"])
            if vals.get("purchase_ref"):
                vals["purchase_ref"] = _normal_ref(vals["purchase_ref"])
        lines = super().create(vals_list)
        lines._place_qty()
        lines._explode()
        return lines

    def write(self, vals):
        self._check_system_fields(vals)
        if {"partner_name", "date", "purchase_ref", "warehouse_id", "tax_id", "reason"} & vals.keys() \
                and not self.env.context.get("odin_stock_book_posting") and self.filtered("stock_purchase_id"):
            raise UserError(_("This line belongs to a whole LPO. Change its supplier, number, date or VAT with "
                              "Edit purchase."))
        if vals.get("purchase_ref"):
            vals["purchase_ref"] = _normal_ref(vals["purchase_ref"])
        if vals.get("partner_name"):
            vals["partner_name"] = _normal_name(vals["partner_name"])
        if "partner_name" in vals and not self.env.context.get("odin_stock_book_posting"):
            # A supplier retyped by hand: the one picked earlier no longer holds.
            self.filtered(lambda l: l.state == "draft").with_context(odin_stock_book_posting=True).write(
                {"partner_id": False})
        if LOCKED_FIELDS & vals.keys() and self.filtered(lambda l: l.state != "draft"):
            raise UserError(_("A posted line cannot be changed. Add a Correction line instead."))
        result = super().write(vals)
        if {"reason", "qty_in", "qty_out"} & vals.keys():
            self._place_qty()
        if ({"product_id", "qty_in", "qty_out", "reason", "bom_id"} & vals.keys() and "component_ids" not in vals
                and not self.env.context.get("odin_stock_exploding")):
            self._explode()
        return result

    def unlink(self):
        if self.filtered(lambda l: l.state == "posted"):
            raise UserError(_("Posted lines cannot be deleted. Add a Correction line instead."))
        return super().unlink()

    # ------------------------------------------------------------ posting
    def _when(self):
        """The line's date as a datetime: now for today, noon (the user's
        time) for a back-dated line, so it lands on its day."""
        today = fields.Date.context_today(self)
        if self.date >= today:
            return fields.Datetime.now()
        tz = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "UTC")
        return tz.localize(datetime.combine(self.date, time(12, 0))).astimezone(pytz.utc).replace(tzinfo=None)

    def _assign_ref(self):
        """The next number for the reason and the line's year, from Odoo's
        gapless sequence: a row lock, so lines posted together or by two
        people at once never share a number, and a failed post gives its
        number back."""
        code = SEQUENCE_CODE % REASONS[self.reason][3]
        ref = self.env["ir.sequence"].next_by_code(code, sequence_date=self.date)
        if not ref:
            raise UserError(_("The numbering for %(reason)s (%(code)s) is missing. Upgrade the Stock Book module.",
                              reason=REASONS[self.reason][1], code=code))
        self.ref = ref

    def action_post(self, allow_short=False):
        if not self.env.user.has_group("odin_stock_book.group_stock_book_supervisor"):
            raise UserError(_("Only a Stock Book supervisor can post. Ask Operations to post it."))
        today = fields.Date.context_today(self)
        drafts = self.filtered(lambda l: l.state == "draft")
        # An LPO is posted whole, or a line left behind could never join it.
        drafts = (drafts | drafts._purchase_siblings()).sorted(lambda l: (l.date, l.id))
        # Received lines posted together for one supplier on one day, under
        # one LPO number or none: one purchase order.
        purchases = {}
        for line in drafts.filtered(lambda l: l.reason == "received"):
            key = line._purchase_key()
            purchases[key] = purchases.get(key, self.browse()) | line
        for line in drafts:
            key, label, _tone, detail = line._status()
            if key != "ready" and not (key == "short" and allow_short):
                raise UserError(_("%(item)s: %(why)s. %(detail)s", item=line.product_id.display_name or _("Line"),
                                  why=label, detail=detail))
            context = {"odin_stock_book_posting": True}
            if line.date < today:
                # Journal entries of a back-dated line go on its day too.
                context["force_period_date"] = line.date
            line = line.with_context(**context)
            if line.reason == "received" and not line.purchase_line_id:
                purchases[line._purchase_key()].with_context(**context)._post_purchase()
            if not line.ref:
                line._assign_ref()
            moves = getattr(line, f"_post_{'make' if line.reason in MAKES else line.reason}")()
            when = line._when()
            moves.write({"date": when})
            moves.move_line_ids.write({"date": when})
            if line.date < today:
                line._backdate_documents(when)
            line.write({
                "state": "posted", "posted_by_id": self.env.user.id, "posted_on": fields.Datetime.now(),
                "move_ids": [Command.set(moves.ids)],
                "onhand_after": line._available(line.product_id, line.warehouse_id)[line.product_id.id],
            })
        return True

    def _backdate_documents(self, when):
        """The manufacturing order, transfer or scrap shows the line's day,
        not the day it was posted."""
        if self.production_id:
            # force_date: Odoo's own way to date a done order.
            self.production_id.with_context(force_date=True).write({"date_start": when, "date_finished": when})
        if self.picking_id:
            self.picking_id.date_done = when
        if self.scrap_id:
            self.scrap_id.date_done = when
        if self.purchase_id:
            self.purchase_id.sudo().date_approve = when

    def _partner(self):
        if self.partner_id:
            # Picked in the purchase window: that contact, not a namesake.
            return self.partner_id
        name = (self.partner_name or "").strip()
        if not name:
            return self.env["res.partner"]
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        Partner = self.env["res.partner"]
        found = Partner.search(
            [("name", "=ilike", escaped), "|", ("company_id", "=", False), ("company_id", "=", self.company_id.id)],
            order="id", limit=20)
        # The company itself rather than a contact person of the same name.
        partner = found.filtered(lambda p: not p.parent_id)[:1] or found[:1]
        if not partner:
            # A name typed in the book is enough for a contact: supervisors
            # need not be allowed to create contacts in general.
            partner = Partner.sudo().create({"name": name.title(), "company_id": False}).sudo(False)
        self.partner_id = partner
        return partner

    def _post_make(self):
        """A manufacturing order for the quantity made, consuming exactly the
        components on the line."""
        mo = self.env["mrp.production"].create({
            "product_id": self.product_id.id, "product_qty": self.qty_in,
            "product_uom_id": self.product_id.uom_id.id, "bom_id": self.bom_id.id,
            "origin": self.ref, "picking_type_id": self.warehouse_id.manu_type_id.id,
            "location_src_id": self._location().id, "location_dest_id": self._location().id,
            "company_id": self.company_id.id,
        })
        mo.action_confirm()
        wanted = {}
        for comp in self.component_ids:
            wanted[comp.product_id] = wanted.get(comp.product_id, 0.0) + comp.qty
        extra = [p for p in wanted if p not in mo.move_raw_ids.product_id]
        if extra:
            mo.write({"move_raw_ids": [Command.create({
                "product_id": p.id, "product_uom_qty": wanted[p], "product_uom": p.uom_id.id,
                "location_id": mo.location_src_id.id, "location_dest_id": p.property_stock_production.id,
                "raw_material_production_id": mo.id, "company_id": mo.company_id.id,
            }) for p in extra]})
            mo.move_raw_ids.filtered(lambda m: m.state == "draft")._action_confirm()
        mo.qty_producing = self.qty_in
        mo._set_qty_producing()
        for move in mo.move_raw_ids:
            qty = wanted.get(move.product_id, 0.0)
            # A component on several recipe lines: the first move takes it all.
            wanted[move.product_id] = 0.0
            move._set_quantity_done(move.product_id.uom_id._compute_quantity(qty, move.product_uom))
            move.picked = True
        result = mo.with_context(skip_consumption=True, skip_backorder=True, mo_ids_to_backorder=[],
                                 skip_redirection=True).button_mark_done()
        if mo.state != "done":
            raise UserError(_("Odoo asked a question while posting %(ref)s (%(what)s). Post it from "
                              "Manufacturing instead.", ref=self.ref,
                              what=isinstance(result, dict) and (result.get("name") or result.get("res_model"))
                              or mo.state))
        self.production_id = mo
        return (mo.move_raw_ids | mo.move_finished_ids).filtered(lambda m: m.state == "done")

    def _post_picking(self, picking_type, src, dest, partner):
        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id, "location_id": src.id, "location_dest_id": dest.id,
            "partner_id": partner.id, "origin": self.ref, "company_id": self.company_id.id,
            "move_ids": [Command.create({
                "product_id": self.product_id.id, "product_uom_qty": self.qty,
                "product_uom": self.product_id.uom_id.id, "location_id": src.id, "location_dest_id": dest.id,
                "company_id": self.company_id.id,
            })],
        })
        picking.action_confirm()
        self._validate_picking(picking)
        self.picking_id = picking
        return picking.move_ids

    def _validate_picking(self, picking):
        """Everything arrived or left as typed: no backorder, no questions."""
        for move in picking.move_ids:
            move._set_quantity_done(move.product_uom_qty)
            move.picked = True
        result = picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids,
                                      skip_sms=True).button_validate()
        if picking.state != "done":
            raise UserError(_("Odoo could not validate %(ref)s (%(what)s).", ref=self[:1].ref,
                              what=isinstance(result, dict) and result.get("name") or picking.state))

    def _post_sale(self):
        wh = self.warehouse_id
        return self._post_picking(wh.out_type_id, self._location(),
                                  self.env.ref("stock.stock_location_customers"), self._partner())

    def _post_return(self):
        wh = self.warehouse_id
        return self._post_picking(wh.in_type_id, self.env.ref("stock.stock_location_customers"),
                                  self._location(), self._partner())

    def _purchase_key(self):
        self.ensure_one()
        # An LPO is one order whatever dates its lines were changed to; without
        # a number, one supplier's lines of one day are.
        if self.stock_purchase_id:
            return ("window", self.stock_purchase_id.id)
        ref = _normal_ref(self.purchase_ref).casefold()
        return (False if ref else self.date, self.company_id.id, self.warehouse_id.id,
                self.partner_id.id or _normal_name(self.partner_name), ref)

    def _purchase_siblings(self):
        """The draft lines of the same LPOs as these lines."""
        lines = self.filtered(lambda l: l.state == "draft" and l.reason == "received" and l.purchase_ref)
        if not lines:
            return self.browse()
        keys = {line._purchase_key() for line in lines}
        return self.search([("state", "=", "draft"), ("reason", "=", "received"),
                            ("purchase_ref", "in", lines.mapped("purchase_ref"))]).filtered(
            lambda l: l._purchase_key() in keys)

    @api.model
    def _check_purchase_ref(self, partner, ref, company):
        """One supplier's number is one purchase, however many times it is
        typed. Odoo only warns of this on a draft bill, once it has happened."""
        ref = _normal_ref(ref)
        if not ref or not partner:
            return
        order = self.env["purchase.order"].sudo().search([
            ("company_id", "=", company.id), ("partner_id", "=", partner.id), ("state", "!=", "cancel"),
            ("partner_ref", "=ilike", _like(ref))], limit=1)
        if order:
            raise UserError(_("%(ref)s from %(supplier)s is already in Purchases as %(order)s, so these goods may "
                              "already be in stock. Check that order; to enter it again, cancel it first.",
                              ref=ref, supplier=partner.display_name, order=order.name))
        bill = self.env["account.move"].sudo().search([
            ("company_id", "=", company.id), ("move_type", "=", "in_invoice"), ("state", "!=", "cancel"),
            ("commercial_partner_id", "=", partner.commercial_partner_id.id), ("ref", "=ilike", _like(ref))], limit=1)
        if bill:
            raise UserError(_("%(ref)s from %(supplier)s is already billed in Accounting as %(bill)s, so it cannot "
                              "be entered again.", ref=ref, supplier=partner.display_name, bill=bill.display_name))

    @api.model
    def _lock_purchase_supplier(self, partner):
        """Two people saving one number at once: the second transaction fails
        on this row, Odoo runs it again, and the number check then sees the
        first one's order. A lock alone would not do: the check would still
        read from before the first one committed."""
        self.env.cr.execute("UPDATE res_partner SET write_date = write_date WHERE id = %s", (partner.id,))

    @api.model
    def _usual_purchase_tax(self, product, partner, company):
        """The VAT last used for this item from this supplier, then for the
        item, then from the supplier; the item's own only if none has been
        bought yet."""
        Order = self.env["purchase.order.line"].sudo()
        base = [("company_id", "=", company.id), ("state", "=", "purchase"), ("tax_ids", "!=", False)]
        searches = []
        if product and partner:
            searches.append([("product_id", "=", product.id), ("partner_id", "=", partner.id)])
        if product:
            searches.append([("product_id", "=", product.id)])
        if partner:
            searches.append([("partner_id", "=", partner.id)])
        for domain in searches:
            line = Order.search(base + domain, order="id desc", limit=1)
            if line:
                return line.tax_ids[:1].sudo(False)
        return product.sudo().supplier_taxes_id._filter_taxes_by_company(company)[:1].sudo(False)

    def _purchase_line_tax(self, partner):
        """The VAT for the order line. Chosen on the line in the purchase
        window. Otherwise the cost was typed before VAT, so the tax must not
        be one included in the price, as many items default to: that would
        read the cost as VAT-inclusive and undervalue the stock."""
        self.ensure_one()
        if self.tax_id:
            return self.tax_id
        tax = self._usual_purchase_tax(self.product_id, partner, self.company_id)
        net = self._net_purchase_tax(tax, self.company_id)
        if net.price_include and net.amount:
            raise UserError(_("%(item)s defaults to %(tax)s, which is included in the price, and no purchase tax of "
                              "the same rate is added on top. Enter it with Add purchase and choose its VAT.",
                              item=self.product_id.display_name, tax=tax.name))
        return net

    @api.model
    def _net_purchase_tax(self, tax, company):
        """The same rate added on top of the price, where the tax given is
        included in it. The tax itself if there is none."""
        if not tax or not tax.price_include:
            return tax
        net = self.env["account.tax"].sudo().search([
            ("company_id", "=", company.id), ("type_tax_use", "=", "purchase"), ("id", "!=", tax.id),
            ("amount_type", "=", tax.amount_type), ("amount", "=", tax.amount)]).filtered(lambda t: not t.price_include)
        return net[:1].sudo(False) or tax

    def _post_purchase(self):
        """One step for goods bought and in the store: a confirmed purchase
        order and its validated receipt, made by Odoo's own purchase flow, so
        the order shows in Purchases ready to bill, the received quantities
        add up, and the order's price values the stock.

        Sudo as the book does for what supervisors need not be allowed in
        general: the user stays the one on the order and in the chatter."""
        head = self[0]
        warehouse = head.warehouse_id
        if warehouse.reception_steps != "one_step":
            raise UserError(_("%(warehouse)s receives in several steps, so goods would not reach stock from here. "
                              "Receive this delivery in Purchases.", warehouse=warehouse.display_name))
        partner = head._partner()
        if head.purchase_ref:
            self._lock_purchase_supplier(partner)
        self._check_purchase_ref(partner, head.purchase_ref, head.company_id)
        taxes = {line.id: line._purchase_line_tax(partner) for line in self}
        for line in self:
            line.partner_id = partner
            line._assign_ref()
        when = head._when()
        window = head.stock_purchase_id
        order = self.env["purchase.order"].sudo().create({
            "partner_id": partner.id, "company_id": head.company_id.id,
            "currency_id": head.company_id.currency_id.id, "picking_type_id": warehouse.in_type_id.id,
            "date_order": when, "origin": ", ".join(self.mapped("ref")), "partner_ref": head.purchase_ref or False,
            "order_line": [Command.create({
                "product_id": line.product_id.id, "product_qty": line.qty_in,
                "product_uom_id": line.product_id.uom_id.id, "price_unit": line.unit_cost, "date_planned": when,
                # Always stated: left out, Odoo takes the item's default tax.
                "tax_ids": [Command.set(taxes[line.id].ids)],
            }) for line in self] + window._charge_order_lines(when),
        })
        # The other costs come after the stock lines, so zip stops at the last item.
        for line, order_line in zip(self, order.order_line):
            # Before the receipt is validated: the link says this receipt is
            # the line's, so it is not logged a second time.
            line.purchase_line_id = order_line.id
        order.button_confirm()
        if order.state != "purchase":
            raise UserError(_("%(order)s needs approval, so it cannot be received from here. Approve it and "
                              "receive it in Purchases.", order=order.display_name))
        picking = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
        if len(picking) != 1:
            raise UserError(_("%(order)s did not make one receipt. Receive it in Purchases.",
                              order=order.display_name))
        self._validate_picking(picking)
        self.write({"picking_id": picking.id})
        if window:
            window._bill_and_pay(order)

    def _post_received(self):
        return self.purchase_line_id.sudo().move_ids.filtered(lambda m: m.state == "done").sudo(False)

    def _post_scrap(self):
        scrap = self.env["stock.scrap"].create({
            "product_id": self.product_id.id, "scrap_qty": self.qty_out,
            "product_uom_id": self.product_id.uom_id.id, "location_id": self._location().id,
            "origin": self.ref, "company_id": self.company_id.id,
        })
        scrap.do_scrap()
        self.scrap_id = scrap
        return scrap.move_ids

    _post_spoiled = _post_scrap
    _post_sample = _post_scrap
    _post_used = _post_scrap

    def _post_correction(self):
        inventory = self.product_id.with_company(self.company_id).property_stock_inventory
        src, dest = (inventory, self._location()) if self.qty_in else (self._location(), inventory)
        move = self.env["stock.move"].create({
            "product_id": self.product_id.id, "product_uom_qty": self.qty, "product_uom": self.product_id.uom_id.id,
            "location_id": src.id, "location_dest_id": dest.id, "origin": self.ref, "is_inventory": True,
            "company_id": self.company_id.id,
        })
        move._action_confirm()
        move._set_quantity_done(self.qty)
        move.picked = True
        move._action_done()
        return move

    def action_cancel(self):
        self.filtered(lambda l: l.state == "draft").with_context(odin_stock_book_posting=True).write(
            {"state": "cancelled"})
        return True

    def action_draft(self):
        """A cancelled line back to a draft. Never a posted one: that is
        undone by the line that does the opposite."""
        self.filtered(lambda l: l.state == "cancelled").with_context(odin_stock_book_posting=True).write(
            {"state": "draft"})
        return True

    def action_open_document(self):
        self.ensure_one()
        purchase = self.purchase_id if self.env.user.has_group("purchase.group_purchase_user") else None
        record = self.production_id or purchase or self.picking_id or self.scrap_id
        if not record:
            return {"type": "ir.actions.act_window", "res_model": "stock.move", "view_mode": "list,form",
                    "domain": [("id", "in", self.move_ids.ids)], "name": self.ref}
        return {"type": "ir.actions.act_window", "res_model": record._name, "res_id": record.id,
                "views": [[False, "form"]]}

    # ------------------------------------------------------------ copy / reverse
    def _copy_as_draft(self, date):
        """The same movement again, as a new draft on ``date``: a filling
        takes the recipe again."""
        self.ensure_one()
        return self.copy({"date": date, "note": False})

    def _reverse(self):
        """A draft that undoes this posted line: a sale comes back as a
        customer return, a scrap or correction as a correction the other
        way. Fillings and receipts are undone in Manufacturing (unbuild) and
        Inventory (return to the supplier)."""
        self.ensure_one()
        if self.state != "posted" or self.reason not in REVERSE:
            raise UserError(_("Only a posted sale, return, scrap or correction can be reversed here."))
        existing = self.search([("reverses_id", "=", self.id), ("state", "!=", "cancelled")], limit=1)
        if existing:
            raise UserError(_("%(ref)s is already reversed by %(other)s.", ref=self.ref,
                              other=existing.ref or _("a draft line")))
        return self.create({
            "date": fields.Date.context_today(self), "warehouse_id": self.warehouse_id.id,
            "company_id": self.company_id.id, "product_id": self.product_id.id,
            "reason": REVERSE[self.reason], "qty_in": self.qty_out, "qty_out": self.qty_in,
            "partner_name": self.partner_name, "reverses_id": self.id,
            "note": _("Reverses %s", self.ref),
        })


class StockBookComponent(models.Model):
    """A component a filling or production line uses: from the recipe, then
    corrected to what was really used."""

    _name = "odin.stock.line.component"
    _description = "Stock Book component"
    _order = "sequence, id"

    line_id = fields.Many2one("odin.stock.line", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer()
    product_id = fields.Many2one("product.product", required=True, domain=[("is_storable", "=", True)])
    uom_id = fields.Many2one(related="product_id.uom_id")
    qty = fields.Float(digits="Product Unit")
    from_recipe = fields.Boolean(readonly=True)

    def _check_draft(self, lines):
        if lines.filtered(lambda l: l.state != "draft"):
            raise UserError(_("A posted line cannot be changed. Add a Correction line instead."))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_draft(self.env["odin.stock.line"].browse({v["line_id"] for v in vals_list if v.get("line_id")}))
        return super().create(vals_list)

    def write(self, vals):
        self._check_draft(self.line_id | self.env["odin.stock.line"].browse(vals.get("line_id")))
        return super().write(vals)

    def unlink(self):
        self._check_draft(self.line_id)
        return super().unlink()
