from odoo import models


class StockRule(models.Model):
    _inherit = 'stock.rule'

    def _get_stock_move_values(self, product_id, product_qty, product_uom, location_dest_id, name, origin, company_id, values):
        """Retarget an imported bar's delivery before anything is reserved.

        The warehouse rule would take the goods out of MS/Stock with the generic
        delivery type. For a POS import, the move is created straight away with
        the bar's SAL operation type and the bar as source location, so:
        - the picking is created (and named) as SAL-BB/BE/MB,
        - kit moves exploded at confirmation copy the bar location,
        - reservation (at confirmation) only ever looks at the bar's stock.
        The destination is the operation type's own (e.g. Customers/BB Sales).
        """
        move_values = super()._get_stock_move_values(
            product_id, product_qty, product_uom, location_dest_id, name, origin, company_id, values)
        bar_id = values.get('pos_import_bar_id')
        if bar_id:
            bar = self.env['odin.bar'].browse(bar_id)
            move_values.update({
                'location_id': bar.location_id.id,
                'picking_type_id': bar.sale_type_id.id,
            })
            if bar.sale_type_id.default_location_dest_id:
                move_values['location_dest_id'] = bar.sale_type_id.default_location_dest_id.id
        return move_values
