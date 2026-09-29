from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    pos_import_id = fields.Many2one('pos.import', string="POS Import", readonly=True, copy=False, index='btree_not_null')
