from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    pos_import_id = fields.Many2one('pos.import', string="POS Import", readonly=True, copy=False, index='btree_not_null')
    pos_import_bar_id = fields.Many2one('odin.bar', string="POS Bar", readonly=True, copy=False)

    def _prepare_confirmation_values(self):
        # An imported day keeps its business date as order date.
        vals = super()._prepare_confirmation_values()
        if self and all(order.pos_import_id for order in self):
            vals.pop('date_order', None)
        return vals

    def _prepare_invoice(self):
        # The day's three bar orders make ONE invoice per day. Odoo 19 groups
        # invoices by shipping address too (see _get_invoice_grouping_keys), and
        # each bar order ships to its own bar, so the consolidated invoice is
        # addressed to the club itself.
        vals = super()._prepare_invoice()
        if self.pos_import_id:
            vals['partner_shipping_id'] = self.partner_invoice_id.id
            vals['ref'] = self.pos_import_id.name
        return vals


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # The bar and the trading day, copied down onto the line and STORED.
    #
    # Both already exist one hop away, on the order, and a plain related field
    # would read them fine. They are stored because reporting cannot: a pivot
    # or any read_group call groups on real columns and will not follow a
    # dotted path, so "sales per bar per day" is not expressible from the line
    # without these. Odoo computes them for existing lines when the module is
    # upgraded.
    #
    # The day comes from the import's business_date rather than date_order: a
    # trading day runs 06:00 to 06:00, so a drink sold at 01:00 belongs to the
    # night before, and only business_date says so without ambiguity.
    pos_import_bar_id = fields.Many2one(
        related='order_id.pos_import_bar_id',
        store=True,
        index='btree_not_null',
        string="POS Bar",
    )
    pos_business_date = fields.Date(
        related='order_id.pos_import_id.business_date',
        store=True,
        index='btree_not_null',
        string="Trading day",
    )

    def _prepare_procurement_values(self):
        values = super()._prepare_procurement_values()
        if self.order_id.pos_import_bar_id:
            values['pos_import_bar_id'] = self.order_id.pos_import_bar_id.id
        return values
