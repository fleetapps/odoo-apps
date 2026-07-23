# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""sale.order / sale.order.line extensions (in-place ``_inherit``).

The Shopify line-item id stored per order line is what makes order-edit
diffing (A2) and fulfillment-order allocation (A1) exact instead of
heuristic.
"""
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    shopify_bisync_instance_id = fields.Many2one(
        "shopify.bisync.instance", string="Shopify Store", readonly=True,
        index="btree_not_null", copy=False)
    shopify_bisync_order_id = fields.Char(
        string="Shopify Order ID", readonly=True, copy=False,
        index="btree_not_null")


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    shopify_bisync_line_id = fields.Char(
        string="Shopify Line ID", readonly=True, copy=False)
