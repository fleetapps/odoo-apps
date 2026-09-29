from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PosImportBar(models.Model):
    _name = 'pos.import.bar'
    _description = "POS Import Bar"
    _order = 'sequence, id'
    _check_company_auto = True

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    pos_group_name = fields.Char(
        string="POS Group", required=True,
        help="Group name exactly as printed on the Group Sales Register, e.g. 'Banda Bar'.")
    pos_suffix = fields.Char(
        string="POS Suffix", required=True,
        help="Code the POS appends to every item of this bar, without brackets: "
             "'BB' for 'Balozi Beer(BB)'.")
    location_id = fields.Many2one(
        'stock.location', string="Bar Location", required=True, check_company=True,
        domain="[('usage', '=', 'internal')]",
        help="Stock is deducted from this location.")
    picking_type_id = fields.Many2one(
        'stock.picking.type', string="Sales Operation", required=True, check_company=True,
        domain="[('code', '=', 'outgoing')]",
        help="Operation type of the bar's daily delivery (e.g. SAL-BB).")
    delivery_partner_id = fields.Many2one(
        'res.partner', string="Delivery Contact", required=True,
        help="Shipping address of the bar's daily sale order.")
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string="Analytic Account", required=True, check_company=True)

    _group_uniq = models.Constraint(
        'UNIQUE(company_id, pos_group_name)',
        "Each POS group can be configured only once per company.")
    _suffix_uniq = models.Constraint(
        'UNIQUE(company_id, pos_suffix)',
        "Each POS suffix can be configured only once per company.")

    @api.constrains('pos_suffix')
    def _check_pos_suffix(self):
        for bar in self:
            if not bar.pos_suffix.isalnum() or bar.pos_suffix != bar.pos_suffix.upper():
                raise ValidationError(self.env._(
                    "The POS suffix of %(bar)s must be letters or digits in capitals, without "
                    "brackets (e.g. BB).", bar=bar.name))

    @api.constrains('location_id', 'picking_type_id')
    def _check_locations(self):
        for bar in self:
            if bar.location_id.usage != 'internal':
                raise ValidationError(self.env._("The location of %(bar)s must be an internal location.", bar=bar.name))
            if bar.picking_type_id.code != 'outgoing':
                raise ValidationError(self.env._("The operation of %(bar)s must be a delivery (outgoing) operation.", bar=bar.name))
