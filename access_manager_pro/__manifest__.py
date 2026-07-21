# Manifest reference: https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Access Manager Pro",
    "version": "19.0.1.0.0",
    "category": "Extra Tools/Security",
    "summary": "No-code access control: hide or make readonly any menu, field, "
               "button, tab, report, export - per user, group and company.",
    "author": "YourCompany",
    "website": "https://yourcompany.example",
    "license": "OPL-1",   # Odoo Proprietary License required for paid store apps
    "price": 399.00,
    "currency": "USD",
    "depends": ["base", "web"],
    "data": [
        "security/access_manager_security.xml",
        "security/ir.model.access.csv",
        "views/access_profile_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        # Assets bundles: https://www.odoo.com/documentation/19.0/developer/reference/frontend/assets.html
        "web.assets_backend": [
            "access_manager_pro/static/src/js/hide_ui_elements.js",
        ],
    },
    "installable": True,
    "application": True,
}
