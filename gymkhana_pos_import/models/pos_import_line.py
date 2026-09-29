from odoo import api, fields, models
from odoo.exceptions import UserError

from .pos_item_map import stock_effect

# Figures read from the PDF: written once when the lines are created, never edited.
PDF_FIELDS = {
    'import_id', 'sequence', 'bar_id', 'group_name', 'sub_group', 'pos_key', 'pos_name',
    'pos_unit', 'bar_suffix', 'item_count', 'rate', 'qty', 'amount', 'disc', 'tax', 'net',
    'page', 'rate_mismatch',
}


class PosImportLine(models.Model):
    _name = 'pos.import.line'
    _description = "POS Import Line"
    _order = 'import_id, sequence, id'
    _rec_name = 'pos_name'

    import_id = fields.Many2one('pos.import', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='import_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='import_id.currency_id')
    sequence = fields.Integer()
    bar_id = fields.Many2one('pos.import.bar', string="Bar", required=True, ondelete='restrict')
    group_name = fields.Char(string="POS Group")
    sub_group = fields.Char(string="Sub Group")
    pos_key = fields.Char(string="POS Key", required=True, index=True)
    pos_name = fields.Char(string="POS Item", required=True)
    pos_unit = fields.Char(string="Unit")
    bar_suffix = fields.Char(string="Suffix")
    item_count = fields.Integer(string="Item Count")
    rate = fields.Monetary()
    qty = fields.Float(string="Quantity", digits='Product Unit')
    amount = fields.Monetary(string="Amount")
    disc = fields.Monetary(string="Discount")
    tax = fields.Monetary(string="Tax")
    net = fields.Monetary(string="Net")
    page = fields.Integer()
    rate_mismatch = fields.Boolean(
        help="Rate x quantity doesn't give the amount printed on the PDF: check the quantity.")
    price_unit = fields.Monetary(string="Unit Price", compute='_compute_price_unit',
                                 help="Net / quantity, tax included.")
    product_id = fields.Many2one(
        'product.product', string="Product",
        domain="[('company_id', 'in', [company_id, False])]")
    match_state = fields.Selection([
        ('ok', "Matched"),
        ('unmatched', "Not matched"),
        ('archived', "Product archived"),
        ('other_company', "Product of another company"),
    ], compute='_compute_match_state', store=True)
    stock_effect = fields.Selection(
        [('storable', "Deducts itself"), ('kit', "Deducts its components"), ('revenue', "Revenue only")],
        compute='_compute_stock_effect')
    price_warning = fields.Char(compute='_compute_price_warning')

    @api.depends('net', 'qty')
    def _compute_price_unit(self):
        for line in self:
            line.price_unit = line.net / line.qty if line.qty else 0.0

    @api.depends('product_id', 'product_id.active', 'product_id.company_id', 'company_id')
    def _compute_match_state(self):
        for line in self:
            product = line.product_id
            if not product:
                line.match_state = 'unmatched'
            elif not product.active:
                line.match_state = 'archived'
            elif product.company_id and product.company_id != line.company_id:
                line.match_state = 'other_company'
            else:
                line.match_state = 'ok'

    @api.depends('product_id.type', 'product_id.is_storable', 'product_id.is_kits')
    def _compute_stock_effect(self):
        for line in self:
            line.stock_effect = stock_effect(line.product_id)

    @api.depends('product_id.lst_price', 'price_unit')
    def _compute_price_warning(self):
        for line in self:
            product = line.product_id
            if product and line.currency_id.compare_amounts(line.price_unit, product.lst_price):
                line.price_warning = self.env._(
                    "PDF price %(pdf)s, Odoo list price %(odoo)s",
                    pdf=f"{line.price_unit:,.2f}", odoo=f"{product.lst_price:,.2f}")
            else:
                line.price_warning = False

    def write(self, vals):
        if PDF_FIELDS & set(vals) and not self.env.context.get('pos_import_system'):
            raise UserError(self.env._("Import lines come from the PDF; only the product can be changed."))
        if 'product_id' in vals and not self.env.context.get('pos_import_system'):
            if any(line.import_id.state != 'review' for line in self):
                raise UserError(self.env._("Products can only be changed while the import is in review."))
            if not self.env.context.get('pos_import_from_mapping'):
                # Every fix is saved to the mapping (which then updates every
                # line with the same POS item in every import under review).
                product = self.env['product.product'].browse(vals['product_id'])
                if not product:
                    raise UserError(self.env._("Choose a product for the POS item."))
                res = super().write(vals)
                done = set()
                for line in self:
                    if (line.company_id, line.pos_key) in done:
                        continue
                    done.add((line.company_id, line.pos_key))
                    self.env['pos.item.map']._pos_remember(
                        line.company_id, line.pos_key, line.pos_name, line.pos_unit, product)
                return res
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('pos_import_system') and \
                any(line.import_id.state != 'draft' for line in self):
            raise UserError(self.env._("Import lines can't be deleted one by one."))
        return super().unlink()
