import base64
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from freezegun import freeze_time
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from ..lib import gymkhana_parser as gp
from . import pdf_tools
from .common import BUSINESS_DATE, NEGATIVE_AT_BANDA, PRICE_CHANGED, PosImportCommon

GROUP_NET = {'BB': 46575.00, 'BE': 162515.00, 'M': 8115.00}
GROUP_AMOUNT = {'BB': 39470.48, 'BE': 137725.51, 'M': 6877.13}
PDF_NET = 217205.00
PDF_TAX = 33131.88
# 23:59 on 27 Sep 2026 in Nairobi (UTC+3) is 20:59 UTC
BUSINESS_DATETIME = datetime(2026, 9, 27, 20, 59)


@tagged('post_install', '-at_install', 'gymkhana_pos_import')
class TestUploadBlocks(PosImportCommon):
    """Screen 1: a problem comes back as one plain-English sentence."""

    def assertUploadRefused(self, data, *fragments, user=None):
        with self.assertRaises(UserError) as cm:
            self._upload(data, user=user)
        for fragment in fragments:
            self.assertIn(fragment, str(cm.exception.args[0]))
        return cm.exception.args[0]

    def test_not_a_sales_register(self):
        self.assertUploadRefused(b"%PDF-1.4 nonsense", "isn't a Gymkhana Group Sales Register")
        self.assertUploadRefused(pdf_tools.replace_text(self.pdf, "Quantity", "Qty", page=1), "column header row")

    def test_more_than_one_day(self):
        data = self.pdf
        for page in range(1, 5):
            data = pdf_tools.replace_text(data, " Date From: 09/27/2026 to 09/27/2026  Outlet:",
                                          " Date From: 09/27/2026 to 09/28/2026  Outlet:", page=page)
        self.assertUploadRefused(data, "must cover exactly one day")

    def test_other_outlet(self):
        self.company.pos_import_outlet = "Elora Ventures Ltd"
        self.assertUploadRefused(self.pdf, "Dhostana Ventures Ltd", "Elora Ventures Ltd")

    @freeze_time('2026-09-26 12:00:00')
    def test_future_date(self):
        self.assertUploadRefused(self.pdf, "is in the future")

    def test_locked_date(self):
        self.company.sudo().sale_lock_date = BUSINESS_DATE
        self.assertUploadRefused(self.pdf, "is locked in accounting")

    def test_totals_dont_add_up(self):
        msg = self.assertUploadRefused(pdf_tools.replace_text(self.pdf, " 56,216.18", " 56,261.18"))
        self.assertEqual(msg, "Sub group Beer/RTD/0.0/Cans(BE): lines add up to 56,216.18 but the report says 56,261.18.")

    def test_last_page_missing(self):
        self.assertUploadRefused(pdf_tools.drop_last_page(self.pdf), "Grand Total missing")

    def test_group_not_configured(self):
        self.bar['M'].active = False
        self.assertUploadRefused(self.pdf, 'The group "Main Bar" isn\'t configured')

    def test_suffix_does_not_match_group(self):
        data = pdf_tools.replace_text(self.pdf, r"Campari\(BE\)", r"Campari\(BB\)")
        self.assertUploadRefused(data, "Campari(BB) is listed under Bulls Eye, whose items end in (BE)")

    def test_date_already_imported(self):
        first = self._upload()
        self.assertEqual(first.state, 'review')
        self.assertUploadRefused(self.pdf, "has already been imported", first.name)
        # the database refuses it too, whatever the code path
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self.env['pos.import'].create({'company_id': self.company.id, 'state': 'review',
                                           'business_date': BUSINESS_DATE, 'file_hash': 'x'})
            self.env.flush_all()

    def test_onchange_gives_instant_feedback(self):
        imp = self.env['pos.import'].with_user(self.pos_user).new({})
        imp.pdf_file = base64.b64encode(pdf_tools.drop_last_page(self.pdf))
        imp._onchange_pdf_file()
        self.assertIn("Grand Total missing", imp.parse_error)
        imp.pdf_file = base64.b64encode(self.pdf)
        imp._onchange_pdf_file()
        self.assertFalse(imp.parse_error)
        self.assertEqual((imp.business_date, imp.pdf_net, imp.pdf_line_count), (BUSINESS_DATE, PDF_NET, 115))


@tagged('post_install', '-at_install', 'gymkhana_pos_import')
class TestReview(PosImportCommon):
    """Screen 2."""

    def test_review_screen(self):
        imp = self._upload()
        self.assertEqual(imp.state, 'review')
        self.assertEqual(len(imp.line_ids), 115)
        self.assertEqual(imp.pdf_net, PDF_NET)
        self.assertEqual(imp.pdf_tax, PDF_TAX)
        self.assertEqual(imp.business_date, BUSINESS_DATE)
        self.assertEqual({bar.pos_suffix: len(lines) for bar, lines in imp._pos_lines_by_bar().items()},
                         {'BB': 32, 'BE': 70, 'M': 13})

        summary = imp.review_summary
        self.assertEqual(summary['title'], f"Sunday 27 Sep 2026 · {self.currency.name} 217,205.00 · 115 lines")
        self.assertEqual([(c['name'], c['total']) for c in summary['bars']],
                         [("Banda Bar", "46,575.00"), ("Bulls Eye", "162,515.00"), ("Main Bar", "8,115.00")])

        # Unmatched items block posting until each is fixed once.
        unmatched_keys = set(self.fixes)
        self.assertEqual(set(imp.unmatched_line_ids.mapped('pos_key')), unmatched_keys)
        self.assertFalse(imp.can_post)
        self.assertNotIn('total', imp.review_detail['money'])  # no money preview while red

        self._fix_unmatched(imp)
        self.assertFalse(imp.unmatched_line_ids)
        self.assertTrue(imp.can_post, imp.review_summary['blocking'])

        money = imp.review_detail['money']
        self.assertEqual(money['total'], "217,205.00")
        self.assertTrue(money['total_matches'])
        self.assertEqual([t['name'] for t in money['taxes']], ["VAT 16%", "2% CTL"])

        # Warnings: shown, not blocking
        warnings = " ".join(imp.review_summary['warnings'])
        self.assertIn("Banda Bar: 1 product(s) go below zero", warnings)
        self.assertIn("priced differently", warnings)
        banda = next(t for t in imp.review_detail['stock'] if t['bar'] == "Banda Bar")
        red = next(r for r in banda['rows'] if r['product'] == NEGATIVE_AT_BANDA)
        self.assertEqual((red['qty'], red['before'], red['after'], red['negative']), ("18.00", "5.00", "-13.00", True))
        self.assertIn(PRICE_CHANGED, [p['product'] for p in imp.review_detail['prices']])
        self.assertEqual(imp.review_detail['records'], {'orders': 3, 'deliveries': ['SAL-BB', 'SAL-BE', 'SAL-MB'], 'invoices': 1})

        # Kits appear as their components in the stock tab
        bulls = next(t for t in imp.review_detail['stock'] if t['bar'] == "Bulls Eye")
        black = next(r for r in bulls['rows'] if r['product'] == "Johnnie Walker Black Label")
        self.assertEqual(black['qty'], "51.00")  # 18 tots + one 1L bottle of 33 tots
        self.assertIn("Black Label (1Ltr)", black['from'])

    def test_fix_is_saved_to_mapping_and_reused(self):
        imp = self._upload()
        kings = imp.unmatched_line_ids.filtered(lambda l: l.pos_key == 'embassy kings|pkt')
        self.assertEqual(kings.bar_id.mapped('pos_suffix'), ['BB', 'BE'])
        kings[0].product_id = self.fixes['embassy kings|pkt']
        # the sibling line of the other bar is fixed by the same choice
        self.assertEqual(kings.mapped('match_state'), ['ok', 'ok'])
        mapping = self.env['pos.item.map'].search([('pos_key', '=', 'embassy kings|pkt')])
        self.assertEqual(mapping.product_id, self.fixes['embassy kings|pkt'])

    def test_archived_or_foreign_product_blocks(self):
        imp = self._upload()
        self._fix_unmatched(imp)
        self.assertTrue(imp.can_post)
        coke = self.env['pos.item.map'].search([('pos_key', '=', 'coke 300 ml|bottle')]).product_id
        coke.active = False
        self.assertEqual(set(imp.line_ids.filtered(lambda l: l.pos_key == 'coke 300 ml|bottle').mapped('match_state')), {'archived'})
        self.assertFalse(imp.can_post)
        with self.assertRaises(UserError):
            imp.action_post()
        coke.active = True
        self.assertTrue(imp.can_post)
        # a product that ends up in another company (one without stock can be moved)
        passion = self.fixes['passion|glass']
        passion.company_id = self.setup_other_company()['company']
        self.assertEqual(set(imp.line_ids.filtered(lambda l: l.pos_key == 'passion|glass').mapped('match_state')),
                         {'other_company'})
        self.assertFalse(imp.can_post)
        with self.assertRaises(UserError):
            imp.action_post()

    def test_lines_are_read_only(self):
        imp = self._upload()
        with self.assertRaises(UserError):
            imp.line_ids[0].qty = 3
        with self.assertRaises(UserError):
            imp.line_ids[0].unlink()

    def test_missing_configuration_blocks(self):
        imp = self._upload()
        self._fix_unmatched(imp)
        self.company.pos_import_tax_ids = [Command.set(self.tax_vat.ids)]
        self.tax_vat.price_include_override = 'tax_excluded'
        self.assertFalse(imp.can_post)
        self.assertIn("price-included", " ".join(imp.review_summary['blocking']))

    def test_bar_without_sales_warns(self):
        main = self.bar['M']
        extra = main.copy({
            'name': "Roma", 'code': 'RM', 'pos_group_name': "Roma", 'pos_suffix': 'RM',
            'location_id': main.location_id.copy({'name': "Roma (RM)"}).id,
        })
        imp = self._upload()
        self._fix_unmatched(imp)
        self.assertIn("Roma has no sales on this day.", imp.review_summary['warnings'])
        self.assertTrue(imp.can_post)
        self.assertTrue(next(c for c in imp.review_summary['bars'] if c['id'] == extra.id)['empty'])


@tagged('post_install', '-at_install', 'gymkhana_pos_import')
class TestPostDay(PosImportCommon):
    """Posting, end to end, on the sample day."""

    def _expected_deductions(self):
        """(bar code, product) -> quantity, worked out independently from the PDF."""
        mapping = {m.pos_key: m.product_id for m in self.env['pos.item.map'].search([])}
        mapping.update(self.fixes)
        suffix = {bar.pos_group_name: bar.pos_suffix for bar in self.bars}
        expected = defaultdict(float)
        for ln in gp.parse(self.pdf)['lines']:
            product = mapping[ln['key']]
            code = suffix[ln['group']]
            if product.is_kits:
                bom = self.env['mrp.bom']._bom_find(product, bom_type='phantom')[product]
                for bom_line in bom.bom_line_ids:
                    expected[code, bom_line.product_id] += float(ln['qty']) * bom_line.product_qty
            elif product.is_storable:
                expected[code, product] += float(ln['qty'])
        return expected

    def _stock_snapshot(self):
        storable = [p for p in self.products.values() if p.is_storable]
        return {
            (loc, product): self._on_hand(product, loc)
            for loc in (self.ms_stock, *self.bars.location_id) for product in storable
        }

    def _fmt(self, amount):
        return f"{amount:,.2f}"

    def _post(self):
        imp = self._upload()
        self._fix_unmatched(imp)
        self.assertTrue(imp.can_post, imp.review_summary['blocking'])
        imp.with_user(self.pos_user).action_post()
        return imp

    def test_post_day(self):
        before = self._stock_snapshot()
        imp = self._post()
        self.assertEqual(imp.state, 'posted')

        # 3 sale orders, one per bar
        orders = imp.order_ids
        self.assertEqual(len(orders), 3)
        self.assertEqual(set(orders.mapped('state')), {'sale'})
        for order in orders:
            bar = order.pos_import_bar_id
            self.assertEqual(order.partner_id, self.club)
            self.assertEqual(order.partner_shipping_id, bar.partner_id)
            self.assertEqual(order.date_order, BUSINESS_DATETIME)
            self.assertAlmostEqual(order.amount_total, GROUP_NET[bar.pos_suffix], places=2)
            for line in order.order_line:
                self.assertEqual(line.analytic_distribution, {str(bar.analytic_account_id.id): 100})
                self.assertEqual(line.tax_ids, self.taxes)
                self.assertEqual(line.qty_delivered, line.product_uom_qty, line.name)
            self.assertFalse(order.order_line.filtered(lambda l: l.product_id == self.company.pos_import_rounding_product_id),
                             "the sample divides exactly: no rounding line")

        # 3 deliveries SAL-BB/BE/MB out of the bars, dated 23:59 Nairobi
        pickings = imp.picking_ids
        self.assertEqual(len(pickings), 3)
        self.assertEqual(set(pickings.mapped('state')), {'done'})
        self.assertEqual(sorted(pickings.picking_type_id.mapped('sequence_code')), ['SAL-BB', 'SAL-BE', 'SAL-MB'])
        for picking in pickings:
            bar = picking.sale_id.pos_import_bar_id
            self.assertIn(f"/{bar.sale_type_id.sequence_code}/", picking.name)
            self.assertEqual(picking.location_id, bar.location_id)
            self.assertEqual(picking.location_dest_id, bar.sale_type_id.default_location_dest_id)
            self.assertEqual(picking.move_ids.location_id, bar.location_id)
            self.assertEqual(picking.move_ids.move_line_ids.location_id, bar.location_id)
            self.assertEqual(picking.date_done, BUSINESS_DATETIME)
            self.assertEqual(set(picking.move_ids.mapped('date')), {BUSINESS_DATETIME})

        # 1 invoice for the day, even though the orders ship to three addresses
        invoice = imp.invoice_id
        self.assertEqual(orders.invoice_ids, invoice)
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(invoice.partner_id, self.club)
        self.assertEqual(invoice.invoice_date, BUSINESS_DATE)
        self.assertEqual(invoice.date, BUSINESS_DATE)
        self.assertEqual(Decimal(str(invoice.amount_total)), Decimal("217205.00"))
        self.assertEqual(set(orders.mapped('invoice_status')), {'invoiced'})
        # tax: Odoo's own computation, within a whisker of the PDF's
        # Tax is Odoo's own computation (rounded per bar); the POS rounds per ticket, so
        # the two differ by a few cents per bar: shown on the review screen for information.
        money = imp.review_detail['money']
        self.assertEqual(money['source'], 'invoice')
        self.assertEqual(money['odoo_tax'], self._fmt(invoice.amount_tax))
        self.assertLessEqual(abs(invoice.amount_tax - PDF_TAX), 0.005 * sum(imp.line_ids.mapped('item_count')))

        # analytic split per bar = the group totals. Each bar is rounded on its own
        # (see PosImport._pos_computation_key), so on the one invoice every bar's
        # revenue and tax are exactly those of its order, whose total is the group's net.
        self.assertAlmostEqual(invoice.amount_untaxed, sum(orders.mapped('amount_untaxed')), places=2)
        self.assertAlmostEqual(invoice.amount_tax, sum(orders.mapped('amount_tax')), places=2)
        analytic = self.env['account.analytic.line'].search([('move_line_id', 'in', invoice.line_ids.ids)])
        for code, bar in self.bar.items():
            order = orders.filtered(lambda o: o.pos_import_bar_id == bar)
            revenue = invoice.invoice_line_ids.filtered(lambda l: l.sale_line_ids.order_id == order)
            self.assertEqual(len(revenue), len(order.order_line), code)
            self.assertEqual(set(revenue.mapped(lambda l: tuple(l.analytic_distribution.items()))),
                             {((str(bar.analytic_account_id.id), 100.0),)}, code)
            self.assertAlmostEqual(-sum(revenue.mapped('balance')), order.amount_untaxed, places=2, msg=code)
            self.assertAlmostEqual(order.amount_untaxed + order.amount_tax, GROUP_NET[code], places=2, msg=code)
            column = bar.analytic_account_id.plan_id._column_name()
            analytic_amount = sum(analytic.filtered(lambda a: a[column] == bar.analytic_account_id).mapped('amount'))
            self.assertAlmostEqual(analytic_amount, -sum(revenue.mapped('balance')), places=2, msg=code)
            # vs the PDF's untaxed group amount: the POS rounds tax per ticket
            tickets = sum(imp.line_ids.filtered(lambda l: l.bar_id == bar).mapped('item_count'))
            self.assertLessEqual(abs(analytic_amount - GROUP_AMOUNT[code]), 0.005 * tickets, code)
        self.assertEqual(len(invoice.invoice_line_ids), 115)

        # stock came off each bar, exactly as the PDF says, and never off MS/Stock
        after = self._stock_snapshot()
        expected = self._expected_deductions()
        for (location, product), qty_before in before.items():
            delta = after[location, product] - qty_before
            if location == self.ms_stock:
                self.assertEqual(delta, 0, f"{product.display_name} moved out of MS/Stock")
            else:
                code = self.bars.filtered(lambda b: b.location_id == location).pos_suffix
                self.assertAlmostEqual(-delta, expected.get((code, product), 0.0), places=4,
                                       msg=f"{product.display_name} at {location.display_name}")
        self.assertEqual(after[self.bar['BB'].location_id, self.products[NEGATIVE_AT_BANDA]], -13)

        # screen 3: the stock outcome is recorded as posted
        banda = next(t for t in imp.review_detail['stock'] if t['bar'] == "Banda Bar")
        self.assertTrue(banda['posted'])
        links = imp.review_detail['records']['links']
        self.assertEqual(len(links['orders']), 3)
        self.assertEqual(len(links['pickings']), 3)
        self.assertEqual(links['invoice'][0]['id'], invoice.id)

    def test_stock_comes_off_the_bar_not_ms_stock(self):
        """Kit components too: the Black Label 1L bottle sold at Bulls Eye."""
        black = self.products["Johnnie Walker Black Label"]
        bulls = self.bar['BE'].location_id
        ms_before, bar_before = self._on_hand(black, self.ms_stock), self._on_hand(black, bulls)
        imp = self._post()
        moves = imp.picking_ids.move_ids.filtered(lambda m: m.product_id == black)
        self.assertEqual(moves.location_id, bulls)
        self.assertEqual(sum(moves.mapped('quantity')), 18 + 33)
        self.assertEqual(self._on_hand(black, self.ms_stock), ms_before)
        self.assertEqual(self._on_hand(black, bulls), bar_before - 51)

    def test_odoo_19_splits_invoices_by_shipping_address(self):
        """Why sale.order._prepare_invoice is extended: without it, three invoices."""
        product = self.products["Coca-Cola 300ml"]
        orders = self.env['sale.order'].create([{
            'partner_id': self.club.id, 'partner_invoice_id': self.club.id,
            'partner_shipping_id': bar.partner_id.id,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 1})],
        } for bar in self.bars])
        orders.action_confirm()
        self.assertEqual(len(orders._create_invoices()), 3)
        # ...and the import's orders make one (asserted in test_post_day)

    def test_pricelist_in_another_currency_is_ignored(self):
        other = self.setup_other_currency('EUR')
        self.club.property_product_pricelist = self.env['product.pricelist'].create({
            'name': "EUR list", 'currency_id': other.id, 'company_id': self.company.id})
        imp = self._post()
        self.assertEqual(imp.order_ids.currency_id, self.currency)
        self.assertEqual(Decimal(str(imp.invoice_id.amount_total)), Decimal("217205.00"))

    def test_rounding_line_when_net_does_not_divide(self):
        # Jagermeister(BB): 7 tots for 1,520.00 -> 217.14 x 7 = 1,519.98 + 0.02 rounding
        data = pdf_tools.replace_text(self.pdf, " 8.00", " 7.00", page=1)
        self.pdf = data
        imp = self._upload(data)
        self.assertIn("Check the quantity of Jagermeister", " ".join(imp.review_summary['warnings']))
        self._fix_unmatched(imp)
        imp.action_post()
        banda = imp.order_ids.filtered(lambda o: o.pos_import_bar_id == self.bar['BB'])
        rounding = banda.order_line.filtered(lambda l: l.product_id == self.company.pos_import_rounding_product_id)
        self.assertEqual(rounding.price_unit, 0.02)
        self.assertAlmostEqual(banda.amount_total, GROUP_NET['BB'], places=2)
        self.assertEqual(Decimal(str(imp.invoice_id.amount_total)), Decimal("217205.00"))

    def test_failure_rolls_everything_back(self):
        imp = self._upload()
        self._fix_unmatched(imp)
        before = self._stock_snapshot()
        orders_before = self.env['sale.order'].search_count([])
        # Make the invoice step fail after orders, deliveries and stock moves exist,
        # by restoring Odoo 19's standard grouping (one invoice per shipping address).
        SaleOrder = type(self.env['sale.order'])
        prepare_invoice = SaleOrder._prepare_invoice

        def standard_grouping(order):
            return dict(prepare_invoice(order), partner_shipping_id=order.partner_shipping_id.id)

        with patch.object(SaleOrder, '_prepare_invoice', standard_grouping):
            with self.assertRaises(UserError) as cm:
                imp.action_post()
        self.assertIn("instead of one", cm.exception.args[0])
        self.assertEqual(imp.state, 'review')
        self.assertFalse(imp.order_ids)
        self.assertFalse(imp.invoice_id)
        self.assertEqual(self.env['sale.order'].search_count([]), orders_before)
        self.assertEqual(self._stock_snapshot(), before)
        # and the day can still be posted afterwards
        imp.action_post()
        self.assertEqual(imp.state, 'posted')

    def test_cannot_post_twice(self):
        imp = self._post()
        with self.assertRaises(UserError):
            imp.action_post()
        self.assertEqual(len(imp.order_ids), 3)

    def test_stored_pdf_must_match_lines(self):
        imp = self._upload()
        self._fix_unmatched(imp)
        imp.line_ids[0].with_context(pos_import_system=True).qty = 99
        with self.assertRaises(UserError) as cm:
            imp.action_post()
        self.assertIn("no longer match the PDF", cm.exception.args[0])

    def test_undo(self):
        before = self._stock_snapshot()
        imp = self._post()
        invoice, orders = imp.invoice_id, imp.order_ids
        with self.assertRaises(AccessError):
            imp.with_user(self.pos_user).action_undo()
        imp.with_user(self.pos_manager).action_undo()
        self.assertEqual(imp.state, 'cancelled')
        self.assertEqual(imp.refund_id.state, 'posted')
        self.assertEqual(imp.refund_id.move_type, 'out_refund')
        self.assertEqual(imp.refund_id.invoice_date, BUSINESS_DATE)
        self.assertEqual(invoice.payment_state, 'reversed')
        self.assertEqual(len(imp.return_picking_ids), 3)
        self.assertEqual(set(imp.return_picking_ids.mapped('state')), {'done'})
        self.assertEqual(set(orders.mapped('state')), {'cancel'})
        self.assertEqual(self._stock_snapshot(), before)
        # the date can be imported again
        again = self._upload()
        self.assertEqual(again.state, 'review')

    def test_undo_refused_once_paid(self):
        imp = self._post()
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=imp.invoice_id.ids).create({})._create_payments()
        self.assertIn(imp.invoice_id.payment_state, ('paid', 'in_payment'))
        self.assertFalse(imp.can_undo)
        with self.assertRaises(UserError):
            imp.with_user(self.pos_manager).action_undo()


@tagged('post_install', '-at_install', 'gymkhana_pos_import')
class TestSeed(PosImportCommon):

    def test_seed_item_map_checks_product_and_name(self):
        Map = self.env['pos.item.map']
        Map.search([]).unlink()
        heineken = self.products["Heineken"]
        other = self.setup_other_company()['company']
        foreign = self.env['product.product'].create({'name': "Pilsner 500ml", 'company_id': other.id})
        created = self.company._pos_import_seed_item_map([
            {'pos_key': 'heineken|bottle', 'pos_name': 'Heineken', 'pos_unit': 'Bottle',
             'product_id': str(heineken.id), 'odoo_product': 'Heineken'},
            {'pos_key': 'tusker lite 330ml|bottle', 'pos_name': 'Tusker Lite 330Ml', 'pos_unit': 'Bottle',
             'product_id': str(heineken.id), 'odoo_product': 'Tusker Lite 330ml'},       # name differs
            {'pos_key': 'pilsner 500ml|bottle', 'pos_name': 'Pilsner 500Ml', 'pos_unit': 'Bottle',
             'product_id': str(foreign.id), 'odoo_product': 'Pilsner 500ml'},           # other company
            {'pos_key': 'embassy kings|pkt', 'pos_name': 'Embassy Kings', 'pos_unit': 'Pkt',
             'product_id': '', 'odoo_product': 'NEEDS PRODUCT'},                        # no product
            {'pos_key': 'heineken|bottle', 'pos_name': 'Heineken', 'pos_unit': 'Bottle',
             'product_id': str(heineken.id), 'odoo_product': 'Heineken'},               # duplicate
        ])
        self.assertEqual(created.mapped('pos_key'), ['heineken|bottle'])
        self.assertEqual(created.product_id, heineken)

    def test_seed_from_csv_keys(self):
        self.assertEqual(len(self.seeded), len([r for r in self.seed_rows if r['status'] == 'mapped']))
        for mapping in self.seeded:
            self.assertEqual(mapping.pos_key, gp.make_key(mapping.pos_name, mapping.pos_unit))


@tagged('post_install', '-at_install', 'gymkhana_pos_import')
class TestInstallHook(PosImportCommon):

    def test_hook_seeds_configuration_without_guessing(self):
        """What the install hook does in the live database, replayed on the test company."""
        self.bars.unlink()
        self.env['pos.item.map'].search([]).unlink()
        self.company.write({
            'pos_import_outlet': False, 'pos_import_partner_id': False,
            'pos_import_tax_ids': [Command.clear()], 'pos_import_rounding_product_id': False,
        })
        self.env['res.company']._pos_import_seed_all()

        self.assertEqual(self.company.pos_import_outlet, "Dhostana Ventures Ltd")
        self.assertEqual(self.company.pos_import_partner_id, self.club)
        self.assertEqual(self.company.pos_import_tax_ids, self.taxes)
        self.assertEqual(self.company.pos_import_rounding_product_id,
                         self.env.ref('gymkhana_pos_import.product_pos_rounding'))
        bars = self.env['odin.bar'].search([('company_id', '=', self.company.id), ('pos_group_name', '!=', False)])
        self.assertEqual(sorted(bars.mapped('pos_suffix')), ['BB', 'BE', 'M'])
        for bar in bars:
            self.assertEqual(bar.location_id.complete_name, f"MS/{bar.name} ({'MB' if bar.pos_suffix == 'M' else bar.pos_suffix})")
            self.assertEqual(bar.sale_type_id.sequence_code, f"SAL-{'MB' if bar.pos_suffix == 'M' else bar.pos_suffix}")
            self.assertEqual(bar.partner_id.parent_id, self.club)
            self.assertEqual(bar.analytic_account_id.name, bar.location_id.name)
        # The CSV's product ids belong to the live database: here they point to other
        # products (or nothing), so no mapping may be created from them.
        for mapping in self.env['pos.item.map'].search([('company_id', '=', self.company.id)]):
            row = next(r for r in self.seed_rows if r['pos_key'] == mapping.pos_key)
            self.assertTrue(row['odoo_product'].startswith(mapping.product_id.name), mapping.pos_key)
