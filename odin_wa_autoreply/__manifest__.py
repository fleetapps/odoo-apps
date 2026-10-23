{
    "name": "WhatsApp Auto-reply",
    "summary": "Answer WhatsApp customers nobody has answered: the quote page that fits "
    "their message, or 'an advisor will reply'; payments and claims escalated at once; the "
    "team chased while they wait",
    "description": """
WhatsApp Auto-reply
===================

At once outside business hours, after 10 minutes during them: the medical or
motor quote page that fits what the customer wrote (signed for their own
lead, so the quote lands there), both when it is not clear, or "an advisor
will reply" for what the pages cannot price. Payments, claims and documents
go to the escalation people at once; existing clients hear from their agent.
After 2 working hours the team is told, and 4 hours before WhatsApp's 24-hour
window closes the escalation people get a to-do. See README.md.
""",
    "version": "19.0.1.0.0",
    "category": "Productivity/Discuss",
    "author": "Fleet Apps",
    "license": "GPL-3",
    "depends": ["whatsapp_connector", "odin_medical_quote_online"],
    "data": [
        "data/ir_cron.xml",
        "views/whatsapp_account_views.xml",
    ],
    "application": False,
    "installable": True,
}
