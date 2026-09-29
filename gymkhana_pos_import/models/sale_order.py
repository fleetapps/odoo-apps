from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    pos_import_id = fields.Many2one('pos.import', string="POS Import", readonly=True, copy=False, index='btree_not_null')
    pos_import_bar_id = fields.Many2one('pos.import.bar', string="POS Bar", readonly=True, copy=False)

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

    def _prepare_procurement_values(self):
        values = super()._prepare_procurement_values()
        if self.order_id.pos_import_bar_id:
            values['pos_import_bar_id'] = self.order_id.pos_import_bar_id.id
        return values
