# -*- coding: utf-8 -*-
"""Seed a sandbox with enough data to actually exercise AI Dashboards.

    odoo shell -d odin_template -c /etc/odoo/odoo.conf --no-http < seed_sandbox.py

Not test fixtures and not a migration — a throwaway pile of plausible sales
history, shaped around the things that are hard to test on a thin database:

* **140 customers**, which is past the pivot's 50-row axis cap, so paging is
  real rather than theoretical.
* **26 months of orders**, so "vs last year" has something on both sides of
  the comparison. A dashboard cannot show a trend against a database that
  only has this week.
* **A dozen customers with no country**, because `in` does not match NULL and
  the Unassigned bucket is exactly the case that silently empties.
* **Lumpy amounts**, so "top 10 by revenue" ranks something meaningful rather
  than an arbitrary slice of near-identical rows.
* **Every order state**, so state filters and the draft/confirmed split have
  something to separate.

Everything it creates is named with the SEED_TAG prefix and nothing else is
touched, so the cleanup at the bottom removes exactly what this added.

Deliberately writes `state` directly instead of calling `action_confirm`.
Confirming a thousand orders would generate a thousand deliveries and take
minutes; dashboards read through `_read_group`, which does not care how a
record reached its state.

NO BLANK LINES INSIDE FUNCTION BODIES — this is piped into an interactive
shell, which treats a blank line as "end of block".
"""
import random
from datetime import date
from dateutil.relativedelta import relativedelta

SEED_TAG = "[SEED]"
CUSTOMERS = 140
NO_COUNTRY = 12          # customers deliberately left without one
PRODUCTS = 15
MONTHS_BACK = 26
ORDERS_PER_MONTH = (30, 55)
LINES_PER_ORDER = (1, 4)
BATCH = 200

random.seed(20260828)    # same pile every run, so results are comparable


def log(message):
    print("  %s" % message)


def wipe(env):
    """Remove everything a previous run added, newest dependency first."""
    log("clearing any previous seed...")
    lines = env["sale.order.line"].search(
        [("order_id.origin", "=", SEED_TAG)])
    lines.unlink()
    orders = env["sale.order"].search([("origin", "=", SEED_TAG)])
    orders.write({"state": "draft"})
    orders.unlink()
    templates = env["product.template"].search(
        [("name", "=like", SEED_TAG + "%")])
    templates.unlink()
    partners = env["res.partner"].search([("ref", "=", SEED_TAG)])
    partners.unlink()
    env.cr.commit()
    log("cleared")


def make_partners(env):
    countries = env["res.country"].search(
        [("code", "in", ["GB", "US", "FR", "DE", "KE", "NL", "ES", "IT",
                         "IE", "CA", "AU", "ZA"])])
    if not countries:
        countries = env["res.country"].search([], limit=12)
    first = ["Northwind", "Acme", "Globex", "Initech", "Umbrella", "Soylent",
             "Vandelay", "Wonka", "Stark", "Wayne", "Cyberdyne", "Tyrell",
             "Gringotts", "Duff", "Bluth", "Prestige", "Hooli", "Pied Piper",
             "Aviato", "Massive Dynamic"]
    second = ["Trading", "Logistics", "Foods", "Systems", "Partners", "Group",
             "Supplies", "Works", "Holdings", "Industries"]
    values = []
    for i in range(CUSTOMERS):
        name = "%s %s %s %s" % (SEED_TAG, random.choice(first),
                                random.choice(second), i + 1)
        # The last NO_COUNTRY customers have no country on purpose: that is the
        # Unassigned row, and it is the one a pivot silently drops if the cell
        # restriction forgets that `in` never matches NULL.
        country = False if i >= CUSTOMERS - NO_COUNTRY else \
            random.choice(countries).id
        values.append({
            "name": name,
            "ref": SEED_TAG,
            "is_company": True,
            "customer_rank": 1,
            "country_id": country,
            "email": "seed%s@example.com" % (i + 1),
        })
    partners = env["res.partner"].create(values)
    log("%s customers (%s with no country)" % (len(partners), NO_COUNTRY))
    return partners


def make_products(env):
    uom = env.ref("uom.product_uom_unit", raise_if_not_found=False)
    names = ["Widget", "Gasket", "Bearing", "Control Unit", "Sensor Array",
             "Pump", "Valve", "Coupling", "Actuator", "Housing", "Relay",
             "Manifold", "Bracket", "Filter", "Drive Belt"]
    values = []
    for i in range(PRODUCTS):
        values.append({
            "name": "%s %s" % (SEED_TAG, names[i % len(names)]),
            "type": "consu",                     # Odoo 19: not "product"
            "is_storable": True,
            "list_price": round(random.uniform(40, 4000), 2),
            "standard_price": round(random.uniform(20, 2000), 2),
            "sale_ok": True,
            "uom_id": uom.id if uom else False,
        })
    templates = env["product.template"].create(
        [{k: v for k, v in row.items() if v is not False or k != "uom_id"}
         for row in values])
    variants = templates.mapped("product_variant_id")
    log("%s products" % len(variants))
    return variants


def make_orders(env, partners, products):
    """Orders spread over MONTHS_BACK months, weighted so revenue has shape."""
    today = date.today()
    states = ["sale"] * 6 + ["done"] * 2 + ["draft"] * 2 + ["sent"] + ["cancel"]
    # A handful of customers get most of the volume, so "top 10" means
    # something. A flat distribution makes every ranking chart identical.
    heavy = random.sample(list(partners), min(12, len(partners)))
    made = 0
    pending = []
    for back in range(MONTHS_BACK):
        month_start = (today - relativedelta(months=back)).replace(day=1)
        # Recent months are busier, which gives year-on-year a real slope.
        weight = 1.0 + (MONTHS_BACK - back) / float(MONTHS_BACK)
        count = int(random.randint(*ORDERS_PER_MONTH) * weight / 1.5)
        for _ in range(count):
            partner = random.choice(heavy) if random.random() < 0.45 \
                else random.choice(partners)
            day = random.randint(1, 28)
            when = month_start.replace(day=day)
            lines = []
            for _line in range(random.randint(*LINES_PER_ORDER)):
                product = random.choice(products)
                lines.append((0, 0, {
                    "product_id": product.id,
                    "product_uom_qty": random.randint(1, 25),
                    "price_unit": round(product.list_price *
                                        random.uniform(0.8, 1.2), 2),
                }))
            pending.append({
                "partner_id": partner.id,
                "date_order": "%s 10:00:00" % when.isoformat(),
                "origin": SEED_TAG,
                "state": random.choice(states),
                "order_line": lines,
            })
            if len(pending) >= BATCH:
                env["sale.order"].create(pending)
                made += len(pending)
                pending = []
                env.cr.commit()
                log("  ... %s orders" % made)
    if pending:
        env["sale.order"].create(pending)
        made += len(pending)
    env.cr.commit()
    log("%s sales orders" % made)
    return made


def summarise(env):
    Order = env["sale.order"]
    total = Order.search_count([("origin", "=", SEED_TAG)])
    confirmed = Order.search_count(
        [("origin", "=", SEED_TAG), ("state", "in", ["sale", "done"])])
    rows = Order._read_group(
        [("origin", "=", SEED_TAG), ("state", "in", ["sale", "done"])],
        groupby=["date_order:year"], aggregates=["amount_total:sum"])
    log("")
    log("what you now have:")
    log("  orders            %s (%s confirmed)" % (total, confirmed))
    for year, amount in rows:
        label = year.year if hasattr(year, "year") else year
        log("  %s revenue      %s" % (label, round(amount or 0, 2)))
    customers = env["res.partner"].search_count([("ref", "=", SEED_TAG)])
    log("  customers         %s" % customers)
    log("")
    log("this exercises: pivot paging (%s customers > 50 cap), year-on-year"
        % customers)
    log("comparison, top-N ranking, the Unassigned bucket, and state filters.")


def run(env):
    log("seeding %s ..." % env.cr.dbname)
    wipe(env)
    partners = make_partners(env)
    products = make_products(env)
    make_orders(env, partners, products)
    summarise(env)
    env.cr.commit()
    log("done — everything above is tagged %s" % SEED_TAG)


run(env)  # noqa: F821 - `env` is provided by `odoo shell`
