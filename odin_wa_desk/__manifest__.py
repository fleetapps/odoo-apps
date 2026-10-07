{
    "name": "WhatsApp Service Requests",
    "summary": "Open a helpdesk ticket from a WhatsApp conversation, and point a "
    "conversation at the client's real contact",
    "description": """
A client asks for a certificate or a member addition on WhatsApp and the request
has to end up somewhere with an owner. Two actions in the conversation header,
next to Create Lead:

* **Link to Client** -- a number WhatsApp has not seen before gets a brand new
  contact, which is a duplicate whenever the client is already on file under
  another number or spelling. Both record-creating actions write to that
  contact, so this one comes first.
* **Create Ticket** -- opens a service request on the conversation's contact and
  links back to the conversation. The conversation stays the only copy of what
  was said; nothing is transcribed into the ticket.
""",
    "version": "19.0.1.0.0",
    "category": "Services/Helpdesk",
    "author": "Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    # helpdesk_mgmt is AGPL-3, so the combined work is too
    "license": "AGPL-3",
    "depends": ["whatsapp_connector", "helpdesk_mgmt"],
    "data": [
        "security/ir.model.access.csv",
        "wizard/wa_link_client_views.xml",
        "views/discuss_channel_views.xml",
        "views/helpdesk_ticket_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odin_wa_desk/static/src/**/*",
        ],
    },
    "application": False,
    "installable": True,
}
