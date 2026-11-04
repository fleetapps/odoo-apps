"""Vouchers are now numbered from a gapless sequence per float, a date range
per month. Give each float its sequence, and start each month's range after
the highest number already given, so numbering carries on where it is."""
import logging
from datetime import date

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Range = env["ir.sequence.date_range"]
    for flt in env["odin.petty.cash.float"].with_context(active_test=False).search([]):
        sequence = flt._voucher_sequence()
        cr.execute("""
            SELECT EXTRACT(YEAR FROM date)::int, EXTRACT(MONTH FROM date)::int, MAX(voucher_seq)
              FROM odin_petty_cash_line WHERE float_id = %s AND voucher_seq IS NOT NULL
             GROUP BY 1, 2""", [flt.id])
        for year, month, last in cr.fetchall():
            first = date(year, month, 1)
            found = Range.search([("sequence_id", "=", sequence.id), ("date_from", "<=", first),
                                  ("date_to", ">=", first)], limit=1) or flt._month_range(first)
            if found.number_next_actual <= last:
                found.number_next_actual = last + 1
            _logger.info("Petty Cash: %s %s-%02d continues at %s", flt.name, year, month, found.number_next_actual)
        cr.execute("""
            SELECT voucher, COUNT(*) FROM odin_petty_cash_line WHERE float_id = %s AND voucher IS NOT NULL
             GROUP BY voucher HAVING COUNT(*) > 1""", [flt.id])
        for voucher, count in cr.fetchall():
            _logger.warning("Petty Cash: %s vouchers share the number %s; renumber them so each is unique.",
                            count, voucher)
