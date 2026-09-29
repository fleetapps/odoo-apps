def post_init_hook(env):
    """Pre-fill the configuration of the Dhostana Ventures company, if this
    database has it: settings, the three bars and the item mapping (from
    data/pos_item_map_seed.csv). Anything that can't be matched with
    certainty is left empty for the user to fill in; nothing is guessed."""
    env['res.company']._pos_import_seed_all()
