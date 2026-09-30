from odoo import api, fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    bar_count_sequence = fields.Integer(
        "Count sheet order", default=100, help="Categories follow this order on the count sheet."
    )


class ProductTemplate(models.Model):
    _inherit = "product.template"

    bar_desk_ok = fields.Boolean("Show in Bar Desk", help="Staff can pick this product in the Bar Desk.")
    bar_count_sequence = fields.Integer(
        "Count sheet order", default=100, help="Order within its category on the count sheet."
    )
    bar_bottle_uom_id = fields.Many2one(
        "uom.uom",
        "Counting bottle",
        compute="_compute_bar_bottle_uom_id",
        store=True,
        readonly=False,
        help="Bottle size staff count in: full bottles first, then the open "
        "bottle in stock units (tots). Leave empty for items counted as units, "
        "such as beer, sodas and wine.",
    )
    tots_per_bottle = fields.Float(
        "Tots per bottle", compute="_compute_tots_per_bottle", digits="Product Unit"
    )
    bar_order_uom_id = fields.Many2one(
        "uom.uom",
        "Order in",
        compute="_compute_bar_order_uom_id",
        store=True,
        readonly=False,
        help="Unit this product is ordered from suppliers in, e.g. Crate of 24 or Bottle 750ml.",
    )
    bar_usual_qty = fields.Float(
        "Usual level at the store",
        digits="Product Unit",
        help="How many (in the order unit) the Main Store should hold after a "
        "supplier delivery. The Desk suggests ordering the difference. Leave 0 "
        "to never suggest it.",
    )

    def _bar_pack_uoms(self):
        """Packagings holding several stock units, smallest first: bottle sizes
        for spirits kept in tots, crates for beer kept in units."""
        self.ensure_one()
        base = self.uom_id
        if not base:
            return self.env["uom.uom"]
        root = (base.parent_path or f"{base.id}/").split("/")[0]
        return self.uom_ids.filtered(
            lambda uom: (uom.parent_path or "").split("/")[0] == root and uom.factor > base.factor
        ).sorted(lambda uom: (uom.factor, uom.id))

    @api.depends("uom_id", "uom_ids")
    def _compute_bar_bottle_uom_id(self):
        unit = self.env.ref("uom.product_uom_unit", raise_if_not_found=False)
        for template in self:
            packs = template._bar_pack_uoms()
            if template.bar_bottle_uom_id and template.bar_bottle_uom_id in packs:
                template.bar_bottle_uom_id = template.bar_bottle_uom_id
            elif template.uom_id == unit:
                # Crates of beer are packs, not bottles to count in.
                template.bar_bottle_uom_id = False
            else:
                template.bar_bottle_uom_id = packs[:1]

    @api.depends("bar_bottle_uom_id", "uom_id", "uom_ids")
    def _compute_bar_order_uom_id(self):
        for template in self:
            allowed = template.uom_id | template._bar_pack_uoms()
            if template.bar_order_uom_id and template.bar_order_uom_id in allowed:
                template.bar_order_uom_id = template.bar_order_uom_id
            else:
                template.bar_order_uom_id = (
                    template.bar_bottle_uom_id or template._bar_pack_uoms()[-1:] or template.uom_id
                )

    @api.depends("bar_bottle_uom_id", "uom_id")
    def _compute_tots_per_bottle(self):
        for template in self:
            bottle = template.bar_bottle_uom_id
            template.tots_per_bottle = (
                bottle._compute_quantity(1, template.uom_id, round=False)
                if bottle and template.uom_id
                else 0.0
            )


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _bar_sort_key(self):
        self.ensure_one()
        return (
            self.categ_id.bar_count_sequence,
            self.categ_id.complete_name or "",
            self.bar_count_sequence,
            self.name or "",
            self.id,
        )

    def _bar_desk_info(self):
        """What the Bar Desk needs to list these products and take quantities."""

        def unit_info(uom, product):
            return {
                "id": uom.id,
                "name": uom.name.split("(")[0].strip() or uom.name,
                "factor": uom._compute_quantity(1, product.uom_id, round=False),
            }

        result = []
        for product in self:
            template = product.product_tmpl_id
            packs = template._bar_pack_uoms()
            bottle = template.bar_bottle_uom_id
            bottles = bottle + (packs - bottle) if bottle else self.env["uom.uom"]
            result.append(
                {
                    "id": product.id,
                    "name": product.display_name,
                    "categ_id": product.categ_id.id,
                    "uom": unit_info(product.uom_id, product),
                    "poured": bool(bottle),
                    "bottles": [unit_info(uom, product) for uom in bottles],
                    "packs": [] if bottle else [unit_info(uom, product) for uom in packs],
                }
            )
        return result
