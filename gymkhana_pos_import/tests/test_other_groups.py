"""Other POS groups: a group of the report that is not a bar of its own.

The club prints the corkage fee under a group "Extras", without a bar
suffix. A bar takes such a group through its "Other POS groups"; an item of
that group that does carry a bar's suffix still goes to that bar.

The report is a spreadsheet built here (see test_sheet_parser), so no club
sales data has to live in the repository.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import PosImportCommon
from .test_sheet_parser import write_xlsx

PERIOD = " Date From: 14/10/2026 to 14/10/2026  Outlet:Dhostana Ventures Ltd  Ignore Item Sales Rate  "
HEADER = {0: "Menu Item", 4: "Unit", 5: "Item Count", 8: "Rate", 10: "Quantity",
          11: "Amount", 12: "Disc. Amt.", 13: "Total Tax", 14: "Net. Amt."}


def item(name, unit, rate, qty, amount, tax, net):
    return {0: name, 4: unit, 5: str(int(qty)), 8: rate, 10: qty, 11: amount, 12: 0.0, 13: tax, 14: net}


def total(label, amount, tax, net):
    return {0: label, 11: amount, 12: 0.0, 13: tax, 14: net}


def report(corkage_bb="Cockage Fee(BB)"):
    """14 Oct 2026: 2 Tusker at Bulls Eye; under Extras, a corkage fee
    without a suffix and one ending in ``corkage_bb``'s suffix."""
    return write_xlsx([
        {0: "Printed By :", 2: "Dhostana Supervisor", 8: "Nairobi Gymkhana"},
        {2: "15/10/2026 09:12:40"},
        {0: "Printed Date :", 7: "Group Sales Register -[ Group,Sub Group And Item Wise Sales Register [Summary] ]"},
        {0: PERIOD},
        HEADER,
        {0: "Group", 2: "Bulls Eye"},
        {0: "Sub Group", 2: "Beer/RTD/0.0/Cans(BE)"},
        item("Tusker Beer 500Ml(BE)", "Bottle", "211.86", 2.0, 423.72, 76.28, 500.0),
        total("Sub Group Total:", 423.72, 76.28, 500.0),
        total("Group Total:", 423.72, 76.28, 500.0),
        {0: "Group", 2: "Extras"},
        {0: "Sub Group", 2: "Extras"},
        item("Cockage Fee", "Each", "847.46", 1.0, 847.46, 152.54, 1000.0),
        item(corkage_bb, "Each", "847.46", 1.0, 847.46, 152.54, 1000.0),
        total("Sub Group Total:", 1694.92, 305.08, 2000.0),
        total("Group Total:", 1694.92, 305.08, 2000.0),
        total("Grand Total:", 2118.64, 381.36, 2500.0),
    ])


@tagged('post_install', '-at_install', 'gymkhana_pos_import')
class TestOtherGroups(PosImportCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = report()
        # Corkage is revenue, not stock: a service.
        cls.corkage = cls._product("Corkage Fee", cls.uom_units, 1000, kind='service')
        cls.env['pos.item.map'].create({
            'company_id': cls.company.id, 'pos_name': "Cockage Fee", 'pos_unit': "Each",
            'product_id': cls.corkage.id})
        cls.tusker = cls.products["Tusker Lager 500ml"]

    def test_group_not_configured_says_how_to_fix_it(self):
        with self.assertRaises(UserError) as cm:
            self._upload(self.report)
        message = cm.exception.args[0]
        self.assertIn('The group "Extras" isn\'t configured', message)
        self.assertIn("Other POS groups", message)

    def test_a_group_belongs_to_one_bar_only(self):
        self.bar['BE'].pos_other_groups = "Extras"
        with self.assertRaises(ValidationError):
            self.bar['BB'].pos_other_groups = "Specials, Extras"
        with self.assertRaises(ValidationError):
            self.bar['BB'].pos_other_groups = "Bulls Eye"
        self.bar['BB'].pos_other_groups = " Specials ,"
        self.assertEqual(self.bar['BB']._pos_other_group_names(), ["Specials"])

    def test_suffix_of_no_bar_is_refused(self):
        self.bar['BE'].pos_other_groups = "Extras"
        with self.assertRaises(UserError) as cm:
            self._upload(report(corkage_bb="Cockage Fee(MB)"))  # Main Bar's suffix is M
        self.assertIn("Cockage Fee(MB) ends in (MB), the POS suffix of no bar", cm.exception.args[0])

    def test_extras_are_posted_with_the_bar_that_takes_them(self):
        bulls, banda, main = self.bar['BE'], self.bar['BB'], self.bar['M']
        bulls.pos_other_groups = "Extras"
        tusker_before = self._on_hand(self.tusker, bulls.location_id)

        imp = self._upload(self.report)
        self.assertEqual(imp.state, 'review')
        self.assertFalse(imp.unmatched_count)
        lines = imp.line_ids.sorted('sequence')
        self.assertEqual([(l.pos_name, l.group_name, l.bar_id) for l in lines], [
            ("Tusker Beer 500Ml", "Bulls Eye", bulls),
            ("Cockage Fee", "Extras", bulls),   # no suffix: the bar that takes Extras
            ("Cockage Fee", "Extras", banda),   # (BB): Banda Bar, whoever takes Extras
        ])
        self.assertEqual(lines.mapped('stock_effect'), ['storable', 'revenue', 'revenue'])

        summary = imp.review_summary
        self.assertFalse(summary['blocking'])
        cards = {c['id']: c for c in summary['bars']}
        self.assertEqual((cards[bulls.id]['total'], cards[bulls.id]['others']), ("1,500.00", "Extras 1,000.00"))
        self.assertEqual((cards[banda.id]['total'], cards[banda.id]['others']), ("1,000.00", "Extras 1,000.00"))
        self.assertTrue(cards[main.id]['empty'])
        self.assertIn("Main Bar has no sales on this day.", summary['warnings'])
        self.assertTrue(imp.review_detail['money']['total_matches'])
        self.assertTrue(imp.can_post)

        imp.with_user(self.pos_user).action_post()
        self.assertEqual(imp.state, 'posted')

        # one order per bar with sales, the corkage on the bar's own order and analytic
        orders = {o.pos_import_bar_id: o for o in imp.order_ids}
        self.assertEqual(set(orders), {bulls, banda})
        self.assertAlmostEqual(orders[bulls].amount_total, 1500.0, places=2)
        self.assertAlmostEqual(orders[banda].amount_total, 1000.0, places=2)
        for bar, order in orders.items():
            corkage = order.order_line.filtered(lambda l: l.product_id == self.corkage)
            self.assertEqual(len(corkage), 1)
            self.assertEqual(corkage.analytic_distribution, {str(bar.analytic_account_id.id): 100})
            self.assertEqual(corkage.qty_delivered, 1.0)
        self.assertEqual(set(imp.order_ids.mapped('invoice_status')), {'invoiced'})

        # stock: only the Tusker left Bulls Eye; corkage moves nothing
        self.assertEqual(imp.picking_ids.picking_type_id, bulls.sale_type_id)
        self.assertFalse(imp.picking_ids.move_ids.filtered(lambda m: m.product_id == self.corkage))
        self.assertEqual(self._on_hand(self.tusker, bulls.location_id), tusker_before - 2)

        # one invoice for the report's grand total
        self.assertEqual(imp.invoice_id.state, 'posted')
        self.assertAlmostEqual(imp.invoice_id.amount_total, 2500.0, places=2)

        # Bar Control: the day is in for every bar, Main Bar without sales
        days = self.env['odin.bar.pos.day'].search([('pos_import_id', '=', imp.id)])
        self.assertEqual({(d.bar_id, d.no_sales) for d in days},
                         {(bulls, False), (banda, False), (main, True)})

    def test_moving_extras_to_another_bar_during_review_blocks_posting(self):
        self.bar['BE'].pos_other_groups = "Extras"
        imp = self._upload(self.report)
        self.bar['BE'].pos_other_groups = False
        self.bar['BB'].pos_other_groups = "Extras"
        with self.assertRaises(UserError) as cm:
            imp.with_user(self.pos_user).action_post()
        self.assertIn("The bar configuration changed", cm.exception.args[0])
        self.assertEqual(imp.state, 'review')
