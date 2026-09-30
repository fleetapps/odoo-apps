{
    "name": "WhatsApp Connector",
    "summary": "One company WhatsApp Business number for many Odoo users: "
    "Enterprise-style conversations in Discuss, or conversations routed to "
    "salespeople as CRM leads",
    "description": """
Connects a WhatsApp Business Platform (Cloud API) number to Odoo.

* No Lead Routing: behaves like Odoo Enterprise's WhatsApp app. A customer's
  message opens a Discuss conversation with the account's Notify users.
* Lead Routing: each new conversation becomes a CRM lead owned by one
  salesperson (round-robin), on the same company number.

Do not install together with Odoo Enterprise's WhatsApp app: both use the
same Discuss channel and message types. See README.md and SPEC.md.
""",
    "version": "19.0.1.0.0",
    "category": "Productivity/Discuss",
    "author": "Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "GPL-3",
    # crm brings mail (Discuss), phone_validation (E.164 numbers and phone
    # search), utm (lead source) and sales_team (Lead Routing).
    "depends": ["crm"],
    "external_dependencies": {"python": ["phonenumbers"]},
    "data": [
        "security/whatsapp_connector_security.xml",
        "security/ir.model.access.csv",
        "data/whatsapp_connector_data.xml",
        "views/whatsapp_account_views.xml",
        "views/whatsapp_template_views.xml",
        "views/whatsapp_message_views.xml",
        "views/whatsapp_webhook_event_views.xml",
        "views/discuss_channel_views.xml",
        "views/menus.xml",
    ],
    "pre_init_hook": "pre_init_hook",
    "application": True,
    "installable": True,
}
