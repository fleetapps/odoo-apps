"""The invoices Stock Book's sale window made before this version kept their
tax invoice number (QuickBooks's) only as their reference. It becomes the
number they print as, so they print as the invoices made from now on do.
Nothing happens where Stock Book is not installed."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("SELECT to_regclass('odin_stock_sale') IS NOT NULL")
    if not cr.fetchone()[0]:
        return
    cr.execute("""
        UPDATE account_move AS move
           SET doc_tax_invoice_no = sale.sale_ref
          FROM odin_stock_sale AS sale
         WHERE sale.invoice_id = move.id
           AND move.doc_tax_invoice_no IS NULL
           AND COALESCE(TRIM(sale.sale_ref), '') != ''
    """)
    _logger.info("Business Documents: %s Stock Book invoices now print their tax invoice number", cr.rowcount)
