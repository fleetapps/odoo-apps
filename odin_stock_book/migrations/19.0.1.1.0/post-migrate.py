"""References now come from one gapless sequence per reason and year. Start
each one after the highest reference already given, so the numbers carry
on from where the book is (renumbered lines included)."""
import logging
import re
from datetime import date

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("SELECT ref FROM odin_stock_line WHERE ref IS NOT NULL")
    last = {}
    for (ref,) in cr.fetchall():
        match = re.fullmatch(r"([A-Z]+)/(\d{4})/(\d+)", ref.strip())
        if match:
            key = (match[1], int(match[2]))
            last[key] = max(last.get(key, 0), int(match[3]))
    Sequence, Range = env["ir.sequence"], env["ir.sequence.date_range"]
    for (prefix, year), number in sorted(last.items()):
        sequence = Sequence.search([("code", "=", f"odin.stock.line.{prefix}")], limit=1)
        if not sequence:
            continue
        day = date(year, 1, 1)
        date_range = Range.search([("sequence_id", "=", sequence.id), ("date_from", "<=", day),
                                   ("date_to", ">=", day)], limit=1) or sequence._create_date_range_seq(day)
        if date_range.number_next_actual <= number:
            date_range.number_next_actual = number + 1
        _logger.info("Stock Book: %s/%s continues at %s", prefix, year, date_range.number_next_actual)
    cr.execute("SELECT ref, COUNT(*) FROM odin_stock_line WHERE ref IS NOT NULL GROUP BY ref HAVING COUNT(*) > 1")
    for ref, count in cr.fetchall():
        _logger.warning("Stock Book: %s lines share the reference %s; renumber them so each is unique.", count, ref)
