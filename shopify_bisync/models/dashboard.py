# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Sales analytics for Shopify orders.

``shopify.bisync.sale.report`` is a read-only SQL view (one row per Shopify
order line) that powers the graph/pivot analytics and the OWL dashboard's
KPI cards: revenue, orders, top products/categories/countries, per-instance
and cross-instance comparison. Modelled on Odoo's own ``sale.report`` view
pattern so it needs no cron to stay fresh.
"""
from odoo import api, fields, models, tools


class ShopifySaleReport(models.Model):
    _name = "shopify.bisync.sale.report"
    _description = "Shopify Sales Analysis"
    _auto = False
    _rec_name = "order_id"
    _order = "date desc"

    order_id = fields.Many2one("sale.order", readonly=True)
    instance_id = fields.Many2one("shopify.bisync.instance", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    date = fields.Datetime(string="Order Date", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    country_id = fields.Many2one("res.country", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    categ_id = fields.Many2one("product.category", string="Category",
                               readonly=True)
    product_tmpl_id = fields.Many2one("product.template", string="Product",
                                      readonly=True)
    state = fields.Selection(
        [("draft", "Quotation"), ("sent", "Quotation Sent"),
         ("sale", "Sales Order"), ("cancel", "Cancelled")], readonly=True)
    qty = fields.Float(string="Qty Ordered", readonly=True)
    price_total = fields.Monetary(string="Total", readonly=True)
    price_subtotal = fields.Monetary(string="Untaxed Total", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    order_count = fields.Integer(string="# Orders", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT l.id AS id,
                       s.id AS order_id,
                       s.shopify_bisync_instance_id AS instance_id,
                       s.company_id AS company_id,
                       s.date_order AS date,
                       s.partner_id AS partner_id,
                       partner.country_id AS country_id,
                       l.product_id AS product_id,
                       t.categ_id AS categ_id,
                       p.product_tmpl_id AS product_tmpl_id,
                       s.state AS state,
                       l.product_uom_qty AS qty,
                       l.price_total AS price_total,
                       l.price_subtotal AS price_subtotal,
                       s.currency_id AS currency_id,
                       1 AS order_count
                  FROM sale_order_line l
                  JOIN sale_order s ON s.id = l.order_id
                  JOIN res_partner partner ON partner.id = s.partner_id
             LEFT JOIN product_product p ON p.id = l.product_id
             LEFT JOIN product_template t ON t.id = p.product_tmpl_id
                 WHERE s.shopify_bisync_instance_id IS NOT NULL
                   AND l.display_type IS NULL
            )
        """)

    @api.model
    def dashboard_data(self, instance_id=None):
        """Aggregates for the OWL dashboard client action. Cross-instance by
        default; scoped when ``instance_id`` is given."""
        domain = [("state", "!=", "cancel")]
        if instance_id:
            domain.append(("instance_id", "=", instance_id))

        def top(group_field, limit=5):
            rows = self._read_group(
                domain, [group_field], ["price_total:sum", "qty:sum"],
                order="price_total:sum desc", limit=limit)
            out = []
            for record, total, qty in rows:
                if record:
                    out.append({"name": record.display_name,
                                "total": total or 0.0, "qty": qty or 0.0})
            return out

        totals = self._read_group(domain, [], ["price_total:sum",
                                              "order_count:sum"])
        revenue, lines = (totals[0] if totals else (0.0, 0))
        orders = self.env["sale.order"].search_count(
            [("shopify_bisync_instance_id", "!=", False),
             ("state", "!=", "cancel")]
            + ([("shopify_bisync_instance_id", "=", instance_id)]
               if instance_id else []))
        by_instance = []
        for record, total in self._read_group(
                [("state", "!=", "cancel")], ["instance_id"],
                ["price_total:sum"], order="price_total:sum desc"):
            if record:
                by_instance.append({"name": record.display_name,
                                    "total": total or 0.0})
        currency = self.env.company.currency_id
        return {
            "currency": {"symbol": currency.symbol,
                         "position": currency.position},
            "revenue": revenue or 0.0,
            "orders": orders,
            "avg_order": (revenue / orders) if orders else 0.0,
            "top_products": top("product_tmpl_id"),
            "top_categories": top("categ_id"),
            "top_countries": top("country_id"),
            "by_instance": by_instance,
        }
