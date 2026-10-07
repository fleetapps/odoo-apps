def post_init_hook(env):
    """Point WhatsApp numbers that predate this module at a team and a channel."""
    env["whatsapp_connector.account"]._odin_seed_desk_defaults()
