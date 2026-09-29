from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_import_outlet = fields.Char(related='company_id.pos_import_outlet', readonly=False)
    pos_import_partner_id = fields.Many2one(related='company_id.pos_import_partner_id', readonly=False)
    pos_import_tax_ids = fields.Many2many(
        related='company_id.pos_import_tax_ids', readonly=False,
        domain="[('type_tax_use', '=', 'sale'), ('company_id', '=', company_id)]")
    pos_import_rounding_product_id = fields.Many2one(
        related='company_id.pos_import_rounding_product_id', readonly=False)
