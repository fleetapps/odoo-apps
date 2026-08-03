# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Access Manager Pro",
    "version": "19.0.3.0.0",
    "category": "Extra Tools/Access Rights",
    "summary": "No-code access control: hide or make read-only any menu, field, "
               "button, tab, report, record or export - per user, group and "
               "company, in the interface and over the API.",
    "description": """
Access Manager Pro
==================

Configure what your users can see and do from a single place - no development
required. Assign an *Access Profile* to any set of users, groups and/or
companies and control:

* Menus and sub-menus (and the Apps installer menu)
* Model buttons: Create, Edit, Delete, Duplicate, Archive, Import, Export
* Fields: invisible, read-only, required, masked, no quick-create, no open
  link, and a domain narrowing what a relational field offers in its dropdown
* View elements: header/statusbar buttons, notebook tabs, kanban links,
  search filters and group-by options (with optional conditions)
* Reports and contextual actions, or the whole Print menu at once
* Chatter: the whole chatter, or Send message / Log note / Activities /
  Followers - with posting and activity scheduling refused at the server, not
  only hidden
* Record-level (conditional) domain restrictions on read/create/edit/delete,
  with a soft (hide-only) option; they hold in pivot and graph totals too
* Read-only users, disabled developer mode, disabled login, and a full block
  on the external API (XML-RPC, JSON-RPC, /json/2 and the user's API keys) for
  people who should only ever work through the interface
* A default profile applied to new users automatically, per user kind
  (internal or portal), so a joiner is covered from their first login

Enforcement is server-side wherever it matters (export, read-only users,
record domains and their aggregates, archive, chatter posting, activity
scheduling, external API, login), with the view layer kept in sync so the UI
never shows what the server would refuse.
""",
    "author": "YourCompany",
    "website": "https://yourcompany.example",
    "license": "OPL-1",  # Odoo Proprietary License (paid App Store module)
    "price": 399.00,
    "currency": "USD",
    "support": "info@yourcompany.example",
    "depends": ["base", "web", "mail"],
    "data": [
        "security/access_manager_groups.xml",
        "security/ir.model.access.csv",
        "data/access_cron.xml",
        "wizard/access_import_wizard_views.xml",
        "views/access_dashboard_views.xml",
        "views/access_profile_views.xml",
        "views/access_manager_menus.xml",
    ],
    "assets": {
        # https://www.odoo.com/documentation/19.0/developer/reference/frontend/assets.html
        "web.assets_backend": [
            "access_manager_pro/static/src/scss/access_manager.scss",
            "access_manager_pro/static/src/js/access_manager.js",
            "access_manager_pro/static/src/scss/access_dashboard.scss",
            "access_manager_pro/static/src/js/dashboard/access_dashboard.js",
            "access_manager_pro/static/src/xml/access_dashboard.xml",
        ],
    },
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": True,
}
