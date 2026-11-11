# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Business Documents",
    "summary": "Every business document printed from the record it belongs to, on the "
    "company letterhead with its own terms: estimate, pro forma, invoice, receipt, credit "
    "note, export commercial invoice and packing list, delivery note, GRN, returns, "
    "transfer form, count sheet, requisition and memo, all found from one app",
    "description": """
The company's own document formats, filled in by Odoo from the record each one
belongs to: an invoice from the invoice, a delivery note from the delivery, a
GRN from the receipt. One Documents app lists them all, says where each is
printed from and opens those records. See README.md.
""",
    "version": "19.0.1.0.0",
    "category": "Productivity",
    "author": "Fleet Apps",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "LGPL-3",
    "depends": ["account", "sale_stock", "purchase_stock", "stock_delivery", "hr", "purchase_request", "mail"],
    "data": [
        "security/documents_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "report/layout.xml",
        "report/report_sales.xml",
        "report/report_export.xml",
        "report/report_stock.xml",
        "report/report_purchase.xml",
        "report/report_internal.xml",
        "report/report_actions.xml",
        "data/document_type_data.xml",
        "views/document_type_views.xml",
        "views/memo_views.xml",
        "views/res_company_views.xml",
        "views/business_views.xml",
        # Menus last: they reference the actions defined above.
        "views/menus.xml",
    ],
    "assets": {
        "web.report_assets_common": [
            "odin_documents/static/src/scss/documents.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": True,
}
