from odoo.exceptions import UserError

# Odoo Enterprise's WhatsApp app. The connector reuses its Discuss channel type
# ('whatsapp') and message type ('whatsapp_message'), so the two must never be
# installed together (SPEC.md §12.1). The name is confirmed in the Enterprise
# Reference Report (§65).
ENTERPRISE_WHATSAPP_MODULE = "whatsapp"


def pre_init_hook(env):
    enterprise = env["ir.module.module"].search([
        ("name", "=", ENTERPRISE_WHATSAPP_MODULE),
        ("state", "in", ("installed", "to install", "to upgrade")),
    ])
    if enterprise:
        raise UserError(env._(
            "WhatsApp Connector cannot be installed while Odoo's own WhatsApp app "
            "is installed: both use the same Discuss conversations and message types.",
        ))
