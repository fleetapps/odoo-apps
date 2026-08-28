# -*- coding: utf-8 -*-
"""Remove everything seed_sandbox.py created, and nothing else.

    odoo shell -d odin_template -c /etc/odoo/odoo.conf --no-http < wipe_sandbox_seed.py

Matches on the same tag the seeder writes — `origin` on orders, `ref` on
partners, a name prefix on products — so a database that also holds real
records keeps them. Orders are put back to draft first because Odoo refuses to
delete a confirmed one, which is the correct protection and the reason a naive
unlink() fails halfway through and leaves a mess.

NO BLANK LINES INSIDE FUNCTION BODIES — piped into an interactive shell.
"""
SEED_TAG = "[SEED]"


def run(env):
    print("  removing %s data from %s ..." % (SEED_TAG, env.cr.dbname))
    lines = env["sale.order.line"].search([("order_id.origin", "=", SEED_TAG)])
    print("  order lines:  %s" % len(lines))
    lines.unlink()
    orders = env["sale.order"].search([("origin", "=", SEED_TAG)])
    print("  orders:       %s" % len(orders))
    # Odoo will not delete a confirmed order; back to draft first.
    orders.write({"state": "draft"})
    orders.unlink()
    templates = env["product.template"].search(
        [("name", "=like", SEED_TAG + "%")])
    print("  products:     %s" % len(templates))
    templates.unlink()
    partners = env["res.partner"].search([("ref", "=", SEED_TAG)])
    print("  customers:    %s" % len(partners))
    partners.unlink()
    env.cr.commit()
    print("  done — nothing outside %s was touched" % SEED_TAG)


run(env)  # noqa: F821 - `env` is provided by `odoo shell`
