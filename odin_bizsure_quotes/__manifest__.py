{
    "name": "BizSure Quotation Catalogue",
    "summary": "The Bizsure rate cards as Odoo data: product categories, the "
    "two Kenyan insurance levies, 278 priced premium products and 16 "
    "quotation templates for APA Jamii Plus and Birdview Bizsure Afya",
    "description": """
BizSure Quotation Catalogue
===========================

Data only -- no code. It gives the sales team a menu to quote from, and gives
``odin_client_quote`` the benefit names, keys and cover limits its client
quotation and cover comparison reports are built on.

What it installs
----------------
* The ``Insurance Premiums`` category tree for APA Jamii Plus, Birdview
  Bizsure Afya and statutory fees.
* **Training Levy 0.2%** and **PHCF 0.25%**, in an ``Insurance Levies`` tax
  group, applied to every premium product that attracts them.
* **278 products**, each priced at its real annual premium from the rate card.
* **16 quotation templates** -- 7 APA Jamii Plus family limits plus child-only,
  and 4 Birdview plans in below-70 and above-70 variants -- carrying 635 lines
  between them: the cover being quoted, the add-ons at zero quantity, the
  benefit notes and the terms.
* Three instalment plans matching the terms the templates quote.

Every section line carries ``benefit_label``, ``benefit_key`` and
``cover_limit``, so a cover comparison lines up Inpatient against Inpatient
across plans and prints what each one pays out. Sections of add-ons are
flagged ``is_optional``.

Generated, not hand-written
---------------------------
``data/source/*.json`` is the rate card; ``tools/generate_data.py`` turns it
into the XML. A rate change is an edit to the JSON and one run of the script.
Do not edit the generated XML, and see README.md before editing prices in
Odoo itself.
""",
    "version": "19.0.1.0.0",
    "category": "Sales/Sales",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["account", "odin_client_quote"],
    "data": [
        "data/product_category.xml",
        "data/account_tax.xml",
        "data/product_template.xml",
        "data/quote_instalment_plan.xml",
        "data/sale_order_template.xml",
    ],
    "application": False,
    "installable": True,
}
