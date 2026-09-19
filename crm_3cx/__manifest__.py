# Copyright (C) Persevida S.L. / FL1 sro, the original `3cxcrm` module (Odoo 14.0 to 18.0).
# Copyright (C) 2026 Odin (fleetapps), the Odoo 19 port and everything beyond lookup.
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
{
    "name": "3CX Phone System Integration",
    "summary": "Caller ID, contact search and click-to-call from the 3CX client, "
               "call and chat logging in the chatter, missed-call follow-up",
    "description": """
Server-side integration with the 3CX Phone System (v18/v20, PRO or Enterprise).

* Caller ID: when a call rings, 3CX asks Odoo who it is and shows the contact
  or lead, with a link that opens the record in Odoo.
* Search Odoo contacts and leads from the 3CX Web Client (by name, company,
  number or email) and call them from there.
* Call logging: every finished call lands in the record's chatter, with the
  text the 3CX administrator configured (agent, direction, duration, recording
  link, and the V20 AI summary, sentiment and transcription when enabled).
* A missed call schedules a "Call back" activity; the next answered call with
  that contact marks it done.
* Chat logging: SMS, WhatsApp and live-chat conversations are logged too.
* Leads from the phone: an agent can create a lead from the 3CX client, and
  calls or chats from unknown numbers can create one automatically.

Started from the free `3cxcrm` module (Persevida S.L. / FL1 sro, AGPL-3),
whose lookup endpoint exists for Odoo 14.0 to 18.0 only.
    """,
    "author": "Odin, Persevida S.L., FL1 sro",
    "website": "https://github.com/fleetapps/odoo-apps",
    "category": "Productivity/VoIP",
    "version": "19.0.1.0.0",
    "license": "AGPL-3",
    # crm brings contacts, mail, calendar and phone_validation; the last one
    # gives res.partner and crm.lead the phone_mobile_search / phone_sanitized
    # fields this module searches and returns.
    "depends": ["crm"],
    "external_dependencies": {"python": ["phonenumbers"]},
    "data": [
        "security/ir.model.access.csv",
        "security/crm_3cx_security.xml",
        "views/crm_3cx_call_views.xml",
        "views/res_users_views.xml",
        "views/res_partner_views.xml",
        "views/crm_lead_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "images": ["static/description/banner.png"],
    # Listed under the default "Apps" filter of the Apps menu, where the person
    # installing it will look for it. It adds no top-level menu of its own.
    "application": True,
    "installable": True,
    "auto_install": False,
}
