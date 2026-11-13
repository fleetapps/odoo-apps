def migrate(cr, version):
    """Suppliers open on prices including VAT: drinks and cigarettes are
    invoiced that way in Kenya. 19.0.1.13.0 opened them on VAT added, before
    any delivery could say otherwise."""
    if version and version.startswith("19.0.1.13."):
        cr.execute("UPDATE res_partner SET bar_invoice_vat = 'included' WHERE bar_invoice_vat = 'added'")
