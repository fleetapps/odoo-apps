import csv
import logging

from odoo import Command, api, fields, models
from odoo.tools.misc import file_path

_logger = logging.getLogger(__name__)

SEED_COMPANY = "Dhostana Ventures Ltd"
SEED_CUSTOMER = "Nairobi Gymkhana"
# (bar name, POS group name, suffix, location, SAL operation type code,
#  delivery contact, analytic account) as configured in the Dhostana database
SEED_BARS = [
    ("Banda Bar", "Banda Bar", "BB", "MS/Banda Bar (BB)", "SAL-BB", "Banda Bar", "Banda Bar (BB)"),
    ("Bulls Eye", "Bulls Eye", "BE", "MS/Bulls Eye (BE)", "SAL-BE", "Bulls Eye", "Bulls Eye (BE)"),
    ("Main Bar", "Main Bar", "M", "MS/Main Bar (MB)", "SAL-MB", "Main Bar", "Main Bar (MB)"),
]
SEED_CSV = "gymkhana_pos_import/data/pos_item_map_seed.csv"


class ResCompany(models.Model):
    _inherit = 'res.company'

    pos_import_outlet = fields.Char(
        string="POS Outlet",
        help="Outlet name exactly as printed on the Group Sales Register "
             "('Outlet:Dhostana Ventures Ltd'). A PDF for another outlet is refused.")
    pos_import_partner_id = fields.Many2one(
        'res.partner', string="POS Customer",
        help="Customer of the daily sale orders and invoice (the club).")
    pos_import_tax_ids = fields.Many2many(
        'account.tax', 'res_company_pos_import_tax_rel', 'company_id', 'tax_id',
        string="POS Taxes",
        help="Taxes applied to every imported line. They must be price-included: "
             "the PDF's net amounts include tax.")
    pos_import_rounding_product_id = fields.Many2one(
        'product.product', string="POS Rounding Product",
        domain="[('type', '=', 'service')]",
        help="Service used for the one-cent line added when a line's net amount "
             "cannot be split exactly into a unit price.")

    # ------------------------------------------------------------------
    # Seeding (post_init_hook)
    # ------------------------------------------------------------------

    @api.model
    def _pos_import_seed_all(self):
        for company in self.sudo().search([('name', '=', SEED_COMPANY)]):
            company._pos_import_seed_settings()
            company._pos_import_seed_bars()
            with open(file_path(SEED_CSV), newline='', encoding='utf-8') as fh:
                company._pos_import_seed_item_map(list(csv.DictReader(fh)))

    def _pos_import_find_one(self, model, domain):
        records = self.env[model].with_context(active_test=True).search(domain, limit=2)
        return records if len(records) == 1 else self.env[model]

    def _pos_import_seed_settings(self):
        self.ensure_one()
        vals = {}
        if not self.pos_import_outlet:
            vals['pos_import_outlet'] = self.name
        if not self.pos_import_partner_id:
            partner = self._pos_import_find_one('res.partner', [
                ('name', '=', SEED_CUSTOMER), ('is_company', '=', True),
                ('company_id', 'in', [self.id, False])])
            if partner:
                vals['pos_import_partner_id'] = partner.id
        if not self.pos_import_tax_ids:
            taxes = self.env['account.tax'].search([
                ('company_id', '=', self.id), ('type_tax_use', '=', 'sale'),
                ('amount_type', '=', 'percent')]).filtered('price_include')
            vat = taxes.filtered(lambda t: t.amount == 16 and 'CTL' not in t.name)
            ctl = taxes.filtered(lambda t: t.amount == 2 and 'CTL' in t.name)
            if len(vat) == 1 and len(ctl) == 1:
                vals['pos_import_tax_ids'] = [Command.set((vat | ctl).ids)]
        if not self.pos_import_rounding_product_id:
            product = self.env.ref('gymkhana_pos_import.product_pos_rounding', raise_if_not_found=False)
            if product:
                vals['pos_import_rounding_product_id'] = product.id
        if vals:
            self.write(vals)

    def _pos_import_seed_bars(self):
        self.ensure_one()
        Bar = self.env['pos.import.bar'].with_context(active_test=False)
        for name, group, suffix, location, sal_code, contact, analytic in SEED_BARS:
            if Bar.search_count([('company_id', '=', self.id), ('pos_group_name', '=', group)]):
                continue
            vals = {
                'name': name, 'pos_group_name': group, 'pos_suffix': suffix, 'company_id': self.id,
                'location_id': self._pos_import_find_one('stock.location', [
                    ('complete_name', '=', location), ('company_id', '=', self.id)]).id,
                'picking_type_id': self._pos_import_find_one('stock.picking.type', [
                    ('sequence_code', '=', sal_code), ('code', '=', 'outgoing'),
                    ('company_id', '=', self.id)]).id,
                'delivery_partner_id': self._pos_import_find_one('res.partner', [
                    ('name', '=', contact), ('parent_id', '=', self.pos_import_partner_id.id)]).id
                    if self.pos_import_partner_id else False,
                'analytic_account_id': self._pos_import_find_one('account.analytic.account', [
                    ('name', '=', analytic), ('company_id', 'in', [self.id, False])]).id,
            }
            if all(vals.values()):
                Bar.create(vals)
            else:
                missing = [k for k, v in vals.items() if not v]
                _logger.warning("POS import: bar %s not seeded for %s, not found: %s", name, self.name, missing)

    def _pos_import_seed_item_map(self, rows):
        """Create one mapping per CSV row whose product_id exists in this
        company and whose name matches the CSV's 'odoo_product' column (which
        may carry a trailing note such as '(kit)'). Rows without a product are
        left for the review screen, where they show up as unmatched."""
        self.ensure_one()
        Map = self.env['pos.item.map'].with_context(active_test=False)
        existing = set(Map.search([('company_id', '=', self.id)]).mapped('pos_key'))
        vals_list = []
        for row in rows:
            key = row['pos_key']
            if key in existing or not (row.get('product_id') or '').strip().isdigit():
                continue
            product = self.env['product.product'].browse(int(row['product_id'])).exists()
            if not product or product.company_id not in (self, self.env['res.company']) \
                    or not (row.get('odoo_product') or '').startswith(product.name):
                _logger.warning("POS import: mapping %s -> product %s not seeded (not found or name differs)",
                                key, row['product_id'])
                continue
            vals_list.append({
                'company_id': self.id, 'pos_name': row['pos_name'],
                'pos_unit': row['pos_unit'], 'product_id': product.id,
            })
            existing.add(key)
        return Map.create(vals_list)
