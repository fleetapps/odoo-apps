"""Run on upgrade what `_post_init_hook` only ever runs on install.

`post_init_hook` fires when a module is installed and never again, so every
setting it seeds is missed by `-u` on an instance that already has the module.
That bit on 2026-09-30: the release adding `bar_supplies` and
`bar_supply_categ_ids` shipped the fourteen Dhostana suppliers in
`SUPPLIER_SEED`, the upgrade ran cleanly, and every one of them came out with
no label and no categories — so `_desk_supplier_products` had nothing to list
for any of them and booking a delivery showed an empty picker.

Both seeds only fill what is empty, so running them again is harmless. Keeping
this in step with `_post_init_hook` means an upgraded instance and a freshly
installed one end up the same, which is the whole point.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env["odin.bar"].search([]).company_id:
        env["odin.bar.reason"]._create_default_reasons(company)
    env["res.partner"]._bar_seed_suppliers()
