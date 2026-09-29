"""A test company built like Dhostana Ventures: three bars with their own
stock locations, SAL operation types, delivery contacts and analytic
accounts; price-included VAT 16% + CTL 2%; and one product for each of the 77
POS items of the sample day (storable items, kits, revenue-only items and a
service), mapped from data/pos_item_map_seed.csv the way the install hook does."""
import base64
import csv
from datetime import date
from pathlib import Path

from odoo import Command
from odoo.tests import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "sales_register_2026-09-27.pdf"
SEED_CSV = HERE.parent / "data" / "pos_item_map_seed.csv"
BUSINESS_DATE = date(2026, 9, 27)

# bar code -> (bar, POS group, location, SAL code)
BARS = {
    'BB': ("Banda Bar", "Banda Bar", "Banda Bar (BB)", "SAL-BB"),
    'BE': ("Bulls Eye", "Bulls Eye", "Bulls Eye (BE)", "SAL-BE"),
    'M': ("Main Bar", "Main Bar", "Main Bar (MB)", "SAL-MB"),
}
# The kits the seed CSV asks for, and the kits already in Odoo: kit -> (component, qty, component UoM)
KITS = {
    "Johnnie Walker Black Label – 1L Bottle": ("Johnnie Walker Black Label", 33, 'tot'),
    "Glenlivet 12 Yrs – 1L Bottle": ("Glenlivet 12 Yrs", 33, 'tot'),
    "Jameson Black Barrel – 750ml Bottle": ("Jameson Black Barrel", 25, 'tot'),
    "House Red Dry – Glass": ("KWV Merlot", 0.2, 'unit'),
    "House Red Sweet – Glass": ("Candy Floss Sweet Red", 0.2, 'unit'),
}
SERVICE_ITEMS = {"Hot Dawa"}           # mapped to a service: revenue only, no delivery move
NEGATIVE_AT_BANDA = "Johnnie Walker Red Label"   # only 5 tots at Banda Bar, 18 are sold
PRICE_CHANGED = "Heineken"             # list price 300 in Odoo, 315 on the PDF


def odoo_name(csv_name):
    """'House Red Dry – Glass (kit)' -> 'House Red Dry – Glass'; notes in [...] dropped."""
    name = csv_name.split("  [")[0].strip()
    return name[:-len(" (kit)")] if name.endswith(" (kit)") else name


class PosImportCommon(AccountTestInvoicingCommon):

    @classmethod
    def get_default_groups(cls):
        return super().get_default_groups() | cls.env.ref('gymkhana_pos_import.group_pos_import_manager')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pdf = FIXTURE.read_bytes()
        cls.company = cls.company_data['company']
        cls.company.name = "Dhostana Ventures Ltd"
        cls.currency = cls.company.currency_id
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.warehouse.view_location_id.name = "MS"  # as in the live database: MS/Banda Bar (BB)
        cls.ms_stock = cls.warehouse.lot_stock_id
        cls.uom_units = cls.env.ref('uom.product_uom_unit')
        cls.uom_tot = cls.env['uom.uom'].create({'name': 'Tot', 'relative_factor': 1.0, 'rounding': 0.01})

        # ---- taxes: both price-included, like the live 16% and 2% CTL ----------
        group_vat = cls.env['account.tax.group'].create({'name': "VAT 16%", 'company_id': cls.company.id})
        group_ctl = cls.env['account.tax.group'].create({'name': "2% CTL", 'company_id': cls.company.id})
        tax_account = cls.company_data['default_account_tax_sale']

        def repartition():
            # like the live taxes: the tax goes to a tax account (tax closing), not to analytic
            return [Command.create({'repartition_type': 'base'}),
                    Command.create({'repartition_type': 'tax', 'account_id': tax_account.id})]
        cls.tax_vat = cls.env['account.tax'].create({
            'name': "16%", 'amount': 16, 'amount_type': 'percent', 'type_tax_use': 'sale',
            'price_include_override': 'tax_included', 'tax_group_id': group_vat.id, 'company_id': cls.company.id,
            'invoice_repartition_line_ids': repartition(), 'refund_repartition_line_ids': repartition(),
        })
        cls.tax_ctl = cls.env['account.tax'].create({
            'name': "2% CTL", 'amount': 2, 'amount_type': 'percent', 'type_tax_use': 'sale',
            'price_include_override': 'tax_included', 'tax_group_id': group_ctl.id, 'company_id': cls.company.id,
            'invoice_repartition_line_ids': repartition(), 'refund_repartition_line_ids': repartition(),
        })
        cls.taxes = cls.tax_vat | cls.tax_ctl

        # ---- the club and its bars ----------------------------------------
        cls.club = cls.env['res.partner'].create({'name': "Nairobi Gymkhana", 'is_company': True, 'company_id': cls.company.id})
        plan = cls.env.ref('analytic.analytic_plan_projects')  # the live bars use the "Project" plan
        customers = cls.env.ref('stock.stock_location_customers')
        cls.bars = cls.env['pos.import.bar']
        for code, (name, group, loc_name, sal_code) in BARS.items():
            location = cls.env['stock.location'].create({
                'name': loc_name, 'usage': 'internal', 'location_id': cls.warehouse.view_location_id.id,
                'company_id': cls.company.id})
            sales = cls.env['stock.location'].create({
                'name': f"{sal_code[4:]} Sales", 'usage': 'customer', 'location_id': customers.id,
                'company_id': cls.company.id})
            picking_type = cls.env['stock.picking.type'].create({
                'name': f"{sal_code[4:]} POS Sales", 'code': 'outgoing', 'sequence_code': sal_code,
                'warehouse_id': cls.warehouse.id, 'company_id': cls.company.id,
                'default_location_src_id': location.id, 'default_location_dest_id': sales.id,
                'reservation_method': 'at_confirm'})
            contact = cls.env['res.partner'].create({'name': name, 'type': 'delivery', 'parent_id': cls.club.id})
            analytic = cls.env['account.analytic.account'].create({
                'name': loc_name, 'plan_id': plan.id, 'company_id': cls.company.id})
            cls.bars |= cls.env['pos.import.bar'].create({
                'name': name, 'pos_group_name': group, 'pos_suffix': code, 'company_id': cls.company.id,
                'location_id': location.id, 'picking_type_id': picking_type.id,
                'delivery_partner_id': contact.id, 'analytic_account_id': analytic.id})
        cls.bar = {bar.pos_suffix: bar for bar in cls.bars}

        cls.company.write({
            'pos_import_outlet': "Dhostana Ventures Ltd",
            'pos_import_partner_id': cls.club.id,
            'pos_import_tax_ids': [Command.set(cls.taxes.ids)],
            'pos_import_rounding_product_id': cls.env.ref('gymkhana_pos_import.product_pos_rounding').id,
        })

        # ---- products, from the seed CSV ------------------------------------
        with SEED_CSV.open(newline='', encoding='utf-8') as fh:
            cls.seed_rows = list(csv.DictReader(fh))
        cls.products = {}
        for row in cls.seed_rows:
            uom = cls.uom_tot if row['pos_unit'].lower() == 'tot' else cls.uom_units
            if row['status'] == 'mapped':
                name = odoo_name(row['odoo_product'])
                if name not in KITS:
                    cls._product(name, uom, row['pos_price_incl'])
        for row in cls.seed_rows:  # kits need their components first
            if row['status'] == 'mapped' and odoo_name(row['odoo_product']) in KITS:
                cls._kit(odoo_name(row['odoo_product']), row['pos_price_incl'])
            elif row['status'] == 'create_kit':
                cls._kit(row['odoo_product'].split(": ", 1)[1].split(" (")[0], row['pos_price_incl'])
            elif row['status'] == 'create_product':
                cls._product(row['pos_name'], cls.uom_units, row['pos_price_incl'])
            elif row['status'] == 'recipe_or_sales_only':
                kind = 'service' if row['pos_name'] in SERVICE_ITEMS else 'revenue'
                cls._product(row['pos_name'], cls.uom_units, row['pos_price_incl'], kind=kind)
        cls.products[PRICE_CHANGED].lst_price = 300.0

        # The rows the CSV maps to an existing product are seeded (as the install
        # hook does in the live database); the others are fixed in the review.
        cls.seeded = cls.company._pos_import_seed_item_map([
            dict(row, product_id=str(cls.products[odoo_name(row['odoo_product'])].id))
            for row in cls.seed_rows if row['status'] == 'mapped'
        ])
        cls.fixes = {}
        for row in cls.seed_rows:
            if row['status'] == 'create_kit':
                cls.fixes[row['pos_key']] = cls.products[row['odoo_product'].split(": ", 1)[1].split(" (")[0]]
            elif row['status'] in ('create_product', 'recipe_or_sales_only'):
                cls.fixes[row['pos_key']] = cls.products[row['pos_name']]

        # ---- stock: plenty in MS/Stock (must not move) and at every bar -------
        Quant = cls.env['stock.quant']
        for product in cls.products.values():
            if product.is_storable:
                Quant._update_available_quantity(product, cls.ms_stock, 1000)
                for bar in cls.bars:
                    Quant._update_available_quantity(product, bar.location_id, 200)
        red_label = cls.products[NEGATIVE_AT_BANDA]
        Quant._update_available_quantity(red_label, cls.bar['BB'].location_id, -195)  # 5 left

        # ---- users -----------------------------------------------------------
        cls.pos_user = new_test_user(
            cls.env, login='pos_user', groups='base.group_user,gymkhana_pos_import.group_pos_import_user',
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.pos_manager = new_test_user(
            cls.env, login='pos_manager', groups='base.group_user,gymkhana_pos_import.group_pos_import_manager',
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])

    @classmethod
    def _product(cls, name, uom, price, kind='storable'):
        vals = {
            'name': name, 'uom_id': uom.id, 'lst_price': float(price),
            'taxes_id': [Command.set(cls.taxes.ids)], 'company_id': cls.company.id,
            'invoice_policy': 'order',
        }
        if kind == 'service':
            vals.update(type='service')
        else:
            vals.update(type='consu', is_storable=kind == 'storable')
        cls.products[name] = cls._create_product(**vals)
        return cls.products[name]

    @classmethod
    def _kit(cls, name, price):
        component_name, qty, uom = KITS[name]
        component = cls.products.get(component_name) or cls._product(
            component_name, cls.uom_tot if uom == 'tot' else cls.uom_units, 0)
        kit = cls._product(name, cls.uom_units, price, kind='revenue')
        cls.env['mrp.bom'].create({
            'product_tmpl_id': kit.product_tmpl_id.id, 'type': 'phantom', 'product_qty': 1,
            'product_uom_id': cls.uom_units.id, 'company_id': cls.company.id,
            'bom_line_ids': [Command.create({
                'product_id': component.id, 'product_qty': qty,
                'product_uom_id': (cls.uom_tot if uom == 'tot' else cls.uom_units).id})],
        })
        return kit

    # ---- helpers ------------------------------------------------------------

    def _upload(self, data=None, user=None):
        """Screen 1: drop the PDF and let it be read (what the drop zone does)."""
        imp = self.env['pos.import'].with_user(user or self.pos_user).create({
            'pdf_file': base64.b64encode(self.pdf if data is None else data),
            'pdf_filename': 'sales_register.pdf',
        })
        imp.action_review()
        return imp

    def _fix_unmatched(self, imp):
        """Screen 2: pick a product for each unmatched item, as in the red banner."""
        for line in imp.unmatched_line_ids:
            if line.match_state != 'ok':  # a fix may already have resolved a sibling
                line.product_id = self.fixes[line.pos_key]

    def _on_hand(self, product, location):
        return sum(self.env['stock.quant'].search([
            ('product_id', '=', product.id), ('location_id', '=', location.id)]).mapped('quantity'))
