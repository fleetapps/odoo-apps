from datetime import datetime

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from .common import BUSINESS_DATE, PosImportCommon

BLACK_LABEL = "Johnnie Walker Black Label"  # 18 tots and one 1L bottle (33 tots) sold at Bulls Eye


@tagged('post_install', '-at_install', 'gymkhana_pos_import')
class TestBarControl(PosImportCommon):
    """What a posted day tells Bar Control: the trading day of each delivery,
    and that the day is in for every bar of the report."""

    def _post(self):
        imp = self._upload()
        self._fix_unmatched(imp)
        self.assertTrue(imp.can_post, imp.review_summary['blocking'])
        imp.with_user(self.pos_user).action_post()
        return imp

    def _pos_days(self):
        return self.env['odin.bar.pos.day'].search([('business_date', '=', BUSINESS_DATE)])

    def test_post_records_the_day_for_every_bar(self):
        main = self.bar['M']
        roma = main.copy({
            'name': "Roma", 'code': 'RM', 'pos_group_name': "Roma", 'pos_suffix': 'RM',
            'location_id': main.location_id.copy({'name': "Roma (RM)"}).id,
        })
        imp = self._post()
        self.assertEqual(set(imp.picking_ids.mapped('bar_business_date')), {BUSINESS_DATE})
        days = self._pos_days()
        self.assertEqual(days.bar_id, self.bars | roma)
        self.assertEqual(days.filtered('no_sales').bar_id, roma, "a bar without a line sold nothing that day")
        self.assertEqual(set(days.mapped('source')), {imp.name})
        self.assertEqual(days.pos_import_id, imp)
        for bar in self.bars | roma:
            self.assertTrue(bar._pos_day_posted(BUSINESS_DATE), bar.name)
        # Bar Control sees exactly what left each bar that day, kits as their components.
        black = self.products[BLACK_LABEL]
        self.assertEqual(self.bar['BE']._pos_moved_qty(black, BUSINESS_DATE), {black.id: 51})
        # A day posted by an import is taken back by undoing the import, not by hand.
        with self.assertRaisesRegex(UserError, "undo that import instead"):
            days[0].unlink()

    def test_undo_takes_the_day_back_out(self):
        imp = self._post()
        imp.with_user(self.pos_manager).action_undo()
        self.assertEqual(set(imp.return_picking_ids.mapped('bar_business_date')), {BUSINESS_DATE})
        self.assertFalse(self._pos_days())
        black = self.products[BLACK_LABEL]
        for bar in self.bars:
            self.assertFalse(bar._pos_day_posted(BUSINESS_DATE), bar.name)
        self.assertEqual(self.bar['BE']._pos_moved_qty(black, BUSINESS_DATE), {black.id: 0})
        # Imported again: the day is back.
        again = self._post()
        self.assertEqual(self._pos_days().pos_import_id, again)
        self.assertEqual(self.bar['BE']._pos_moved_qty(black, BUSINESS_DATE), {black.id: 51})

    def test_bar_setup_is_checked_before_posting(self):
        imp = self._upload()
        self._fix_unmatched(imp)
        self.bar['BB'].partner_id = False
        self.assertFalse(imp.can_post)
        self.assertIn("Set the POS delivery contact of Banda Bar under Bar Control > Configuration > Bars.",
                      imp.review_summary['blocking'])
        with self.assertRaisesRegex(UserError, "POS delivery contact of Banda Bar"):
            imp.action_post()

    def test_a_waiting_count_opens_the_import(self):
        bar = self.bar['BE'].with_user(self.pos_user)
        action = bar._pos_import_action(BUSINESS_DATE)
        self.assertEqual(action['res_model'], 'pos.import')
        self.assertFalse(action.get('res_id'), "a new import")
        imp = self._upload()
        self.assertEqual(bar._pos_import_action(BUSINESS_DATE)['res_id'], imp.id)
        clerk = new_test_user(self.env, login='stock_clerk', groups='stock.group_stock_user',
                              company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        self.assertFalse(self.bar['BE'].with_user(clerk)._pos_import_action(BUSINESS_DATE))

    def test_with_the_bar_desk(self):
        """Last night's closing count waits for the import, is shown ready on
        the import screen once the day is posted, settles on the import's
        deductions, and goes back for approval when the day is undone."""
        if 'odin.bar.count' not in self.env:
            self.skipTest("the Bar Desk is not installed")
        be = self.bar['BE']
        black = self.products[BLACK_LABEL]
        submitted_at = datetime(2026, 9, 27, 21, 30)  # 00:30 at the bar: still Sunday's trading day
        before = be._stock_as_of(black, submitted_at, BUSINESS_DATE)[black.id]
        count = self.env['odin.bar.count'].sudo().create({
            'bar_id': be.id, 'kind': 'closing', 'business_date': BUSINESS_DATE,
            'line_ids': [Command.create({'product_id': black.id, 'touched': True, 'unit_qty': before - 51})],
        })
        count.write({'state': 'submitted', 'submitted_at': submitted_at})
        self.assertIn("Post the POS import for Bulls Eye on Sun 27 Sep", count.approve_blocked_reason)

        imp = self._post()
        self.assertEqual([link['id'] for link in imp.review_summary['bar_links']], count.ids)
        count.invalidate_recordset()
        self.assertFalse(count.approve_blocked_reason)
        count.action_approve()
        line = count.line_ids
        self.assertEqual((line.expected_qty, line.diff_qty), (before - 51, 0))

        imp.invalidate_recordset(['review_summary'])
        self.assertFalse(imp.review_summary['bar_links'])
        self.assertEqual([link['id'] for link in imp.review_summary['undo_links']], count.ids)
        imp.with_user(self.pos_manager).action_undo()
        self.assertEqual(count.state, 'submitted')
        self.assertIn(count.name, imp.message_ids[0].body)
