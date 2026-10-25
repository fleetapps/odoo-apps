# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Kenya - eTIMS",
    "countries": ["ke"],
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Sign Kenyan invoices and credit notes with KRA eTIMS from Odoo "
               "Community: KRA's OSCU directly, through the etims library, or "
               "through DigiTax, with a queue that never loses an invoice number.",
    "description": """
Kenya - eTIMS
=============

KRA eTIMS e-invoicing for Kenyan companies on Odoo 19 Community. Companies of
any other country in the same database are not touched: their invoices post,
print and send exactly as before.

Posting a customer invoice or credit note queues it; the queue signs it
within a minute, retries with back-off, and holds the KRA invoice number
across retries so a sale is never signed twice or skipped. The signed receipt
-- KRA invoice and receipt numbers, internal data, signature, SCU ID and QR
code -- is on the invoice's eTIMS tab and printed on the invoice PDF.

Four interchangeable backends, chosen per device (one device per KRA branch):

* **KRA OSCU (direct)** -- our own client for KRA's OSCU API. Production
  needs KRA's certification of the integration.
* **KRA OSCU via etims library** -- the same API through the MIT-licensed
  ``etims`` package. Experimental; optional; reported unavailable when the
  package is not installed.
* **DigiTax** -- DigiTax's eTIMS REST API, TEST or LIVE key.
* **Demo** -- deterministic fake receipts for demos and tests, watermarked
  "DEMO - NOT A TAX INVOICE".

A vendor bill from a seller who cannot issue eTIMS invoices -- a small-scale
farmer -- can be signed as a reverse invoice instead, where the backend offers
it (DigiTax).

Built on Kenya Tax Base, so the KRA tax type an invoice line transmits under is
the one its VAT3 box is derived from. See README.md.
""",
    "author": "Fleet",
    "website": "https://fleet.ke",
    "support": "developers@fleet.ke,support@odin.ist,andrew@fleet.ke",
    "maintainer": "Fleet",
    "license": "LGPL-3",
    "depends": ["account", "l10n_ke", "x_ke_base"],
    # The etims package is optional and imported lazily: without it the
    # "KRA OSCU via etims library" backend reports itself unavailable.
    "external_dependencies": {"python": ["requests", "qrcode"]},
    "data": [
        "security/x_ke_etims_security.xml",
        "security/ir.model.access.csv",
        "data/ke_etims_code_data.xml",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "views/ke_etims_device_views.xml",
        "views/ke_etims_code_views.xml",
        "views/ke_etims_document_views.xml",
        "views/ke_etims_log_views.xml",
        "views/account_move_views.xml",
        "views/account_move_reversal_views.xml",
        "views/account_tax_views.xml",
        "views/product_template_views.xml",
        "views/res_partner_views.xml",
        "views/res_company_views.xml",
        "views/res_config_settings_views.xml",
        "report/report_invoice.xml",
        # Menus last: they reference the actions defined above.
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
