{
    "name": "WhatsApp Service Requests",
    "summary": "Turn a WhatsApp conversation into a tracked helpdesk ticket in one click",
    "description": """
A client asks for something on WhatsApp -- a certificate, a member addition, a
claim -- and the request has to end up somewhere with an owner and a history.
Retyping it into a ticket is work nobody does under pressure, so the request
stays in the chat and is lost when the person who read it goes on leave.

This adds a "Log as Request" button to a WhatsApp conversation. It opens a
helpdesk ticket on the same contact, carries the recent messages over as the
description so nothing is retyped, and notes the ticket number back in the
conversation as an internal note -- never as a message to the customer.
""",
    "version": "19.0.1.0.0",
    "category": "Services/Helpdesk",
    "author": "Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "GPL-3",
    "depends": ["whatsapp_connector", "helpdesk_mgmt"],
    "data": [
        "views/discuss_channel_views.xml",
        "views/helpdesk_ticket_views.xml",
    ],
    "application": False,
    "installable": True,
}
