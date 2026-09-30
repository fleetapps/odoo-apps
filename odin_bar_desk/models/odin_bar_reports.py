"""Read-only reports of Bar Control: stock per location and its history.

Both are SQL views over Odoo's own stock records, limited to the stock
locations of the bars and stores, so they always agree with Inventory.
Quantities come in the product's stock unit (tots, units) and in its bulk
unit (bottles for spirits, crates for beer and sodas).
"""

from odoo import fields, models, tools
from odoo.tools import SQL

HISTORY_KINDS = [
    ("pos", "POS sales"),
    ("move", "Moved by hand"),
    ("supplier", "Supplier delivery"),
    ("count", "Count adjustment"),
    ("adjustment", "Stock adjustment"),
    ("scrap", "Scrap"),
    ("other", "Other transfer"),
]


def _bar_locations_sql():
    """Every bar or store with the path of its stock location, to match the
    location and its sub-locations."""
    return """
        SELECT bar.id AS bar_id, bar.company_id, bar.tz, bar.day_start_hour, location.parent_path
          FROM odin_bar bar
          JOIN stock_location location ON location.id = bar.location_id
         WHERE bar.active
    """


class OdinBarStockReport(models.Model):
    _name = "odin.bar.stock.report"
    _description = "Stock by Location"
    _auto = False
    _order = "bar_id, categ_id, product_id"

    bar_id = fields.Many2one("odin.bar", string="Location", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", string="Product template", readonly=True)
    categ_id = fields.Many2one("product.category", string="Category", readonly=True)
    quantity = fields.Float("On hand", digits="Product Unit", readonly=True, help="In the stock unit: tots, units.")
    uom_id = fields.Many2one("uom.uom", string="Unit", readonly=True)
    bulk_qty = fields.Float(
        "Bottles / crates", digits=(16, 1), readonly=True, help="On hand in the bulk unit: bottles or crates."
    )
    bulk_uom_id = fields.Many2one("uom.uom", string="Bulk unit", readonly=True)
    value = fields.Monetary(readonly=True, currency_field="currency_id", help="On hand at the product's cost.")
    currency_id = fields.Many2one(related="company_id.currency_id")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            SQL(
                """
                CREATE VIEW %s AS (
                    WITH bars AS (%s)
                    SELECT MIN(quant.id) AS id,
                           bars.bar_id,
                           bars.company_id,
                           quant.product_id,
                           template.id AS product_tmpl_id,
                           template.categ_id,
                           template.uom_id,
                           SUM(quant.quantity) AS quantity,
                           template.bar_bulk_uom_id AS bulk_uom_id,
                           SUM(quant.quantity) * uom.factor / COALESCE(NULLIF(bulk.factor, 0), uom.factor) AS bulk_qty,
                           SUM(quant.quantity) * COALESCE((product.standard_price ->> bars.company_id::text)::numeric, 0) AS value
                      FROM stock_quant quant
                      JOIN stock_location location ON location.id = quant.location_id
                      JOIN bars ON location.parent_path LIKE bars.parent_path || '%%'
                      JOIN product_product product ON product.id = quant.product_id
                      JOIN product_template template ON template.id = product.product_tmpl_id
                      JOIN uom_uom uom ON uom.id = template.uom_id
                 LEFT JOIN uom_uom bulk ON bulk.id = template.bar_bulk_uom_id
                  GROUP BY bars.bar_id, bars.company_id, quant.product_id, product.standard_price, template.id,
                           template.categ_id, template.uom_id, template.bar_bulk_uom_id, uom.factor, bulk.factor
                )
                """,
                SQL.identifier(self._table),
                SQL(_bar_locations_sql()),
            )
        )


class OdinBarStockHistory(models.Model):
    _name = "odin.bar.stock.history"
    _description = "Stock History"
    _auto = False
    _order = "date desc, id desc"

    date = fields.Datetime(readonly=True)
    business_date = fields.Date("Trading day", readonly=True)
    bar_id = fields.Many2one("odin.bar", string="Location", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    kind = fields.Selection(HISTORY_KINDS, readonly=True)
    direction = fields.Selection([("in", "In"), ("out", "Out")], readonly=True)
    other_location_id = fields.Many2one("stock.location", string="From / to", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    categ_id = fields.Many2one("product.category", string="Category", readonly=True)
    quantity = fields.Float(
        digits="Product Unit", readonly=True, help="In the stock unit: positive in, negative out."
    )
    uom_id = fields.Many2one("uom.uom", string="Unit", readonly=True)
    bulk_qty = fields.Float("Bottles / crates", digits=(16, 2), readonly=True)
    bulk_uom_id = fields.Many2one("uom.uom", string="Bulk unit", readonly=True)
    reference = fields.Char(readonly=True)
    picking_id = fields.Many2one("stock.picking", string="Transfer", readonly=True)
    move_id = fields.Many2one("stock.move", readonly=True)
    employee_id = fields.Many2one("hr.employee", string="Logged by", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Partner", readonly=True)
    out_reason_id = fields.Many2one("odin.bar.reason", string="Out of the club", readonly=True)
    variance_reason_id = fields.Many2one("odin.bar.variance.reason", string="Variance reason", readonly=True)
    count_id = fields.Many2one("odin.bar.count", string="Count", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            SQL(
                """
                CREATE VIEW %s AS (
                    WITH bars AS (%s),
                    lines AS (
                        SELECT ml.id * 2 AS id, ml.id AS line_id, bars.*, 'out' AS direction,
                               -ml.quantity_product_uom AS quantity, ml.location_dest_id AS other_location_id
                          FROM stock_move_line ml
                          JOIN stock_location source ON source.id = ml.location_id
                          JOIN stock_location dest ON dest.id = ml.location_dest_id
                          JOIN bars ON source.parent_path LIKE bars.parent_path || '%%'
                         WHERE dest.parent_path NOT LIKE bars.parent_path || '%%'
                        UNION ALL
                        SELECT ml.id * 2 + 1 AS id, ml.id AS line_id, bars.*, 'in' AS direction,
                               ml.quantity_product_uom AS quantity, ml.location_id AS other_location_id
                          FROM stock_move_line ml
                          JOIN stock_location source ON source.id = ml.location_id
                          JOIN stock_location dest ON dest.id = ml.location_dest_id
                          JOIN bars ON dest.parent_path LIKE bars.parent_path || '%%'
                         WHERE source.parent_path NOT LIKE bars.parent_path || '%%'
                    )
                    SELECT lines.id,
                           ml.date,
                           COALESCE(
                               picking.bar_business_date,
                               bar_count.business_date,
                               ((ml.date AT TIME ZONE 'UTC' AT TIME ZONE COALESCE(lines.tz, 'UTC'))
                                 - make_interval(secs => COALESCE(lines.day_start_hour, 0) * 3600))::date
                           ) AS business_date,
                           lines.bar_id,
                           lines.company_id,
                           CASE
                               WHEN picking.bar_manual_move THEN 'move'
                               WHEN move.bar_count_id IS NOT NULL THEN 'count'
                               WHEN picking.bar_business_date IS NOT NULL THEN 'pos'
                               WHEN move.scrap_id IS NOT NULL THEN 'scrap'
                               WHEN move.is_inventory THEN 'adjustment'
                               WHEN picking_type.code = 'incoming' THEN 'supplier'
                               ELSE 'other'
                           END AS kind,
                           lines.direction,
                           lines.other_location_id,
                           move.product_id,
                           template.categ_id,
                           lines.quantity,
                           template.uom_id,
                           lines.quantity * uom.factor / COALESCE(NULLIF(bulk.factor, 0), uom.factor) AS bulk_qty,
                           template.bar_bulk_uom_id AS bulk_uom_id,
                           move.reference,
                           move.picking_id,
                           move.id AS move_id,
                           picking.bar_employee_id AS employee_id,
                           picking.partner_id,
                           picking.bar_reason_id AS out_reason_id,
                           move.bar_variance_reason_id AS variance_reason_id,
                           move.bar_count_id AS count_id
                      FROM lines
                      JOIN stock_move_line ml ON ml.id = lines.line_id
                      JOIN stock_move move ON move.id = ml.move_id
                 LEFT JOIN stock_picking picking ON picking.id = move.picking_id
                 LEFT JOIN stock_picking_type picking_type ON picking_type.id = picking.picking_type_id
                 LEFT JOIN odin_bar_count bar_count ON bar_count.id = move.bar_count_id
                      JOIN product_product product ON product.id = move.product_id
                      JOIN product_template template ON template.id = product.product_tmpl_id
                      JOIN uom_uom uom ON uom.id = template.uom_id
                 LEFT JOIN uom_uom bulk ON bulk.id = template.bar_bulk_uom_id
                     WHERE move.state = 'done'
                )
                """,
                SQL.identifier(self._table),
                SQL(_bar_locations_sql()),
            )
        )

    def action_open_source(self):
        """Open the transfer, count or adjustment this line comes from."""
        self.ensure_one()
        if self.picking_id:
            return {"type": "ir.actions.act_window", "res_model": "stock.picking", "res_id": self.picking_id.id, "views": [(False, "form")]}
        if self.count_id:
            return {"type": "ir.actions.act_window", "res_model": "odin.bar.count", "res_id": self.count_id.id, "views": [(False, "form")]}
        return {"type": "ir.actions.act_window", "res_model": "stock.move", "res_id": self.move_id.id, "views": [(False, "form")]}
