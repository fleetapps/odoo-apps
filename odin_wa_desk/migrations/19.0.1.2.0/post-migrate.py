from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Seed the Service Desk settings on an existing install.

    post_init_hook runs on install only, and the module is already installed
    everywhere it matters -- without this the new settings would come up empty
    on exactly the databases that have numbers configured.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["whatsapp_connector.account"]._odin_seed_desk_defaults()
