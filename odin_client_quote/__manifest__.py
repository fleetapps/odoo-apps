{
    "name": "Client Quotations & Cover Comparison",
    "summary": "Quotation PDFs written for the client, not the back office: "
    "benefits and limits instead of product codes, optional cover priced "
    "separately instead of padding the bill with zero-quantity rows, and a "
    "side-by-side comparison of several plans on one page",
    "description": """
Client Quotations & Cover Comparison
====================================

Two reports and the small amount of data they need.

**Client quotation** (``sale.order``) replaces the stock quotation PDF for
documents the customer actually reads. It prints what is being bought and what
it costs, and nothing else: lines quoted at zero quantity move out of the price
table into a separate "Optional cover you can add" price list, product codes
are stripped from descriptions, and benefit notes are set as readable lists
instead of paragraphs squeezed into a Quantity / Unit Price / Taxes / Amount
grid.

**Cover comparison** (``odin.quote.comparison``) puts several quotations for
the same customer side by side -- one column per plan, one row per benefit --
so the customer can compare limits and premiums across plans without opening
four separate PDFs. Every figure is read live from its quotation; nothing is
retyped.

Both reports read the cover limit and the client-facing benefit name from the
section lines of the quotation, so a section carries two things at once: what
the plan pays out (``cover_limit``) and what the client pays in (the section
total). See README.md.
""",
    "version": "19.0.1.5.0",
    "category": "Sales/Sales",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["sale_management"],
    "data": [
        "security/ir.model.access.csv",
        "data/quote_comparison_data.xml",
        "report/paperformat.xml",
        "report/quote_styles.xml",
        "report/report_saleorder_client.xml",
        "report/report_quote_comparison.xml",
        "report/report_actions.xml",
        "views/product_template_views.xml",
        "views/sale_order_views.xml",
        "views/sale_order_template_views.xml",
        "views/quote_instalment_plan_views.xml",
        "views/quote_comparison_views.xml",
        "views/menus.xml",
    ],
    "application": True,
    "installable": True,
}
