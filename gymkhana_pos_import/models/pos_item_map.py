from odoo import api, fields, models

from ..lib.gymkhana_parser import make_key


def stock_effect(product):
    """How selling ``product`` affects stock; the product type decides, no extra fields.

    - storable product: deducts itself
    - kit (phantom BoM): deducts its components
    - anything else (non-storable goods, services): records revenue only
    """
    if not product:
        return False
    if product.type == 'consu' and product.is_kits:
        return 'kit'
    if product.is_storable:
        return 'storable'
    return 'revenue'


class PosItemMap(models.Model):
    _name = 'pos.item.map'
    _description = "POS Item Mapping"
    _order = 'pos_name, pos_unit, id'
    _rec_name = 'pos_name'
    _check_company_auto = True

    pos_key = fields.Char(
        string="POS Key", compute='_compute_pos_key', store=True, precompute=True, index=True,
        help="Normalised name|unit, see make_key() in the parser. Shared by all bars.")
    pos_name = fields.Char(string="POS Item", required=True)
    pos_unit = fields.Char(string="POS Unit")
    product_id = fields.Many2one('product.product', string="Product", required=True, check_company=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    stock_effect = fields.Selection(
        [('storable', "Deducts itself"), ('kit', "Deducts its components"), ('revenue', "Revenue only")],
        compute='_compute_stock_effect')

    _key_uniq = models.Constraint(
        'UNIQUE(company_id, pos_key)',
        "This POS item is already mapped (possibly in an archived mapping).")

    @api.depends('product_id.type', 'product_id.is_storable', 'product_id.is_kits')
    def _compute_stock_effect(self):
        for mapping in self:
            mapping.stock_effect = stock_effect(mapping.product_id)

    @api.depends('pos_name', 'pos_unit')
    def _compute_pos_key(self):
        for mapping in self:
            mapping.pos_key = make_key(mapping.pos_name or '', mapping.pos_unit or '')

    @api.model_create_multi
    def create(self, vals_list):
        mappings = super().create(vals_list)
        mappings._pos_apply_to_reviews()
        return mappings

    def write(self, vals):
        res = super().write(vals)
        if {'product_id', 'active', 'pos_name', 'pos_unit'} & set(vals):
            self._pos_apply_to_reviews()
        return res

    def _pos_apply_to_reviews(self):
        """Every import still in review picks up the new mapping at once."""
        for mapping in self.filtered('active'):
            lines = self.env['pos.import.line'].search([
                ('company_id', '=', mapping.company_id.id),
                ('pos_key', '=', mapping.pos_key),
                ('import_id.state', '=', 'review'),
                ('product_id', '!=', mapping.product_id.id),
            ])
            lines.with_context(pos_import_from_mapping=True).write({'product_id': mapping.product_id.id})

    @api.model
    def _pos_remember(self, company, key, name, unit, product):
        """Save a product choice for a POS key (create, update or un-archive)."""
        mapping = self.with_context(active_test=False).search(
            [('company_id', '=', company.id), ('pos_key', '=', key)], limit=1)
        if mapping:
            if mapping.product_id != product or not mapping.active:
                mapping.write({'product_id': product.id, 'active': True})
        else:
            mapping = self.create({
                'company_id': company.id, 'pos_name': name, 'pos_unit': unit, 'product_id': product.id,
            })
            if mapping.pos_key != key:
                raise ValueError(f"POS key mismatch: {mapping.pos_key!r} != {key!r}")
        return mapping
