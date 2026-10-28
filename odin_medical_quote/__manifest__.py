{
    "name": "Medical Quotes",
    "summary": "Add a medical quote on one screen: who is covered, the plans "
    "inside the client's budget, every cover option priced for that family, "
    "and the chosen plans side by side with discount, levies and total. "
    "Saving puts the client and the opportunity in CRM; when they accept a "
    "plan it is confirmed and invoiced",
    "description": """
Medical Quotes
==============

The quick way to quote family medical cover, for an agent on a call.

1. **Client and cover details.** A new prospect, an existing customer or a
   CRM lead; the principal's date of birth, spouse, number of children; the
   inpatient limit range the client can afford.
2. **Cover options.** Every plan inside the range that covers this family,
   with each option already priced for them: inpatient, outpatient, dental,
   optical, maternity, riders. Plans the family cannot take say why.
3. **Compare.** The chosen plans side by side as cards: each benefit and its
   premium, a discount (% or amount), taxes and levies, a policy charge and
   the total.
4. **Save.** The client is added to Contacts, the opportunity to the CRM
   pipeline with a follow-up, one quotation per plan is linked to it, and
   the medical quotation is ready to print, email or share on WhatsApp.

When the client accepts a plan, **Client accepted** confirms that quotation,
cancels the others, marks the opportunity won and creates the invoice -- the
full premium or a deposit. **Client declined** marks the opportunity lost and
cancels the quotations.

Premiums come from the rate cards already in Odoo: quotation templates and
their products. How each product is priced (by family size, per person by
role and age band, or once per policy) is read from its name and can be
corrected on the product. See README.md.
""",
    "version": "19.0.1.2.2",
    "category": "Sales/Sales",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["odin_client_quote", "sale_crm"],
    "data": [
        "security/ir.model.access.csv",
        "data/product_data.xml",
        "report/medical_quotation.xml",
        "report/insurance_invoice.xml",
        "data/mail_template_data.xml",
        "data/mail_activity_data.xml",
        "views/product_template_views.xml",
        "views/sale_order_template_views.xml",
        "views/medical_quote_actions.xml",
        "wizard/quote_accept_views.xml",
        "views/quote_comparison_views.xml",
        "views/crm_lead_views.xml",
        "views/res_config_settings_views.xml",
        "views/cover_product_views.xml",
        "wizard/cover_placed_views.xml",
        "views/renewal_views.xml",
        "data/renewal_data.xml",
        "data/cover_product_data.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_medical_quote/static/src/**/*",
        ],
        "web.assets_tests": [
            "odin_medical_quote/static/tests/tours/**/*",
        ],
    },
    "application": False,
    "installable": True,
}
