"""End-to-end check on a COPY of the production database, with its real
products, kits, bars and stock. It posts a day and checks the result; like
every Odoo test it runs in a transaction that is rolled back at the end, but
run it on a copy anyway:

    createdb -T <production_db> pos_copy        # or duplicate it from the database manager
    odoo-bin -d pos_copy --stop-after-init --test-tags pos_import_livecopy
    # another day: POS_IMPORT_PDF=/path/to/Group_Sales_Register.pdf odoo-bin ...

It is skipped (with the reason) when the day can't be posted yet, e.g. while
some POS items still need a product: fix them in a review first.
"""
import base64
import os
from collections import defaultdict
from pathlib import Path

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

HERE = Path(__file__).resolve().parent


@tagged('post_install', '-at_install', '-standard', 'pos_import_livecopy')
class TestLiveCopy(TransactionCase):

    def test_post_a_real_day(self):
        pdf = Path(os.environ.get('POS_IMPORT_PDF') or HERE / 'fixtures' / 'sales_register_2026-09-27.pdf')
        company = self.env['res.company'].search([('pos_import_outlet', '!=', False)], limit=1)
        if not company:
            self.skipTest("No company has the POS import configured.")
        env = self.env(context=dict(self.env.context, allowed_company_ids=company.ids))
        imp = env['pos.import'].create({
            'company_id': company.id, 'pdf_file': base64.b64encode(pdf.read_bytes()), 'pdf_filename': pdf.name})
        try:
            imp.action_review()
        except UserError as e:
            self.skipTest(f"The PDF was refused: {e.args[0]}")
        if imp.unmatched_count:
            items = sorted({f"{l.pos_name} ({l.pos_unit})" for l in imp.unmatched_line_ids})
            self.skipTest(f"{len(items)} POS item(s) still need a product: {', '.join(items)}")
        if not imp.can_post:
            self.skipTest("Blocked: " + "; ".join(imp.review_summary['blocking']))

        # What each bar should lose, worked out independently with Odoo's BoM explosion
        expected = defaultdict(float)
        for line in imp.line_ids:
            product = line.product_id
            bom = env['mrp.bom']._bom_find(product, company_id=company.id, bom_type='phantom')[product]
            if bom:
                _boms, exploded = bom.explode(product, product.uom_id._compute_quantity(line.qty, bom.product_uom_id) / bom.product_qty)
                for bom_line, data in exploded:
                    if bom_line.product_id.is_storable:
                        qty = bom_line.product_uom_id._compute_quantity(data['qty'], bom_line.product_id.uom_id, round=False)
                        expected[line.bar_id, bom_line.product_id] += qty
            elif product.is_storable:
                expected[line.bar_id, product] += line.qty
        products = env['product.product'].union(*(p for _b, p in expected))
        bars = imp.line_ids.bar_id
        warehouses_stock = bars.picking_type_id.warehouse_id.lot_stock_id

        def on_hand(location, product):
            return sum(env['stock.quant'].search([
                ('location_id', 'child_of', location.id), ('product_id', '=', product.id)]).mapped('quantity'))

        before = {(loc, p): on_hand(loc, p) for loc in bars.location_id | warehouses_stock for p in products}

        imp.action_post()

        self.assertEqual(imp.state, 'posted')
        self.assertEqual(len(imp.order_ids), len(bars))
        invoice = imp.invoice_id
        self.assertEqual(imp.order_ids.invoice_ids, invoice, "one invoice for the day")
        self.assertEqual(invoice.currency_id.compare_amounts(invoice.amount_total, imp.pdf_net), 0)
        self.assertEqual(invoice.invoice_date, imp.business_date)
        for bar in bars:
            order = imp.order_ids.filtered(lambda o: o.pos_import_bar_id == bar)
            group_net = sum(imp.line_ids.filtered(lambda l: l.bar_id == bar).mapped('net'))
            self.assertEqual(order.currency_id.compare_amounts(order.amount_total, group_net), 0, bar.name)
            revenue = invoice.invoice_line_ids.filtered(lambda l: l.sale_line_ids.order_id == order)
            self.assertAlmostEqual(-sum(revenue.mapped('balance')), order.amount_untaxed, places=2, msg=bar.name)
            for picking in order.picking_ids:
                self.assertEqual(picking.picking_type_id, bar.picking_type_id)
                self.assertEqual(picking.move_ids.location_id, bar.location_id)
        for location in warehouses_stock:
            for product in products:
                self.assertAlmostEqual(on_hand(location, product), before[location, product], places=4,
                                       msg=f"{product.display_name} left {location.display_name}")
        for bar in bars:
            for product in products:
                delta = before[bar.location_id, product] - on_hand(bar.location_id, product)
                self.assertAlmostEqual(delta, expected.get((bar, product), 0.0), places=2,
                                       msg=f"{product.display_name} at {bar.name}")
