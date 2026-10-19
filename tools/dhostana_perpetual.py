"""Move a bar company from periodic to perpetual stock valuation, in phases.

Run from `odoo shell`, one phase at a time, reading the report each time
before going on:

    PHASE=check  odoo shell -c /etc/odoo/odoo.conf -d <db> --no-http \\
        --workers 0 --max-cron-threads 0 < tools/dhostana_perpetual.py

Nothing is written unless APPLY=1. Every phase is idempotent and prints what
it would do, so `check` can be run on production any day of the week.

    PHASE=check     what the database looks like now; always safe
    PHASE=config    locations and categories (step A)
    PHASE=costs     product cost from purchase history, then kits from BoM
    PHASE=restate   historical move values, and the margins that follow
    PHASE=opening   the figures for the opening entry -- printed, never posted

Order matters. `config` arms the journal entries, so it must come after the
costs are right and after the opening position has been agreed, or stock
starts moving with nothing behind it. The phases refuse to run out of order
where they can tell.

Why this is a script and not a module migration: it moves money, it is
specific to one company, and it must never fire again on a routine upgrade.
"""

import os

from odoo.tools import float_round

APPLY = os.environ.get("APPLY") == "1"
PHASE = os.environ.get("PHASE", "check")
COMPANY = os.environ.get("COMPANY", "Dhostana Ventures Ltd")

# Where each kind of outflow lands. All but Stock Variation are
# expense_direct_cost, so they sit inside Cost of Revenue on the P&L.
ACCOUNTS = {
    "interim_received": "100110",   # the receipt leg, cleared by the bill
    "variance": "500200",           # counted short or over
    "scrap": "500300",              # written off
    "given_away": "500400",         # Roma, Events, unpaid bills
}

company = env["res.company"].search([("name", "=", COMPANY)], limit=1)
if not company:
    raise SystemExit(f"No company named {COMPANY!r}")
env = env(context=dict(env.context, allowed_company_ids=[company.id]))
Location = env["stock.location"].with_company(company)
log = []


def say(line=""):
    log.append(str(line))


def account(code):
    found = env["account.account"].search(
        [("code", "=", code), ("company_ids", "in", company.id)], limit=1
    )
    if not found:
        raise SystemExit(f"Account {code} not found in {company.name}. Create it first.")
    return found


def money(value):
    return f"{float_round(value, precision_digits=2):,.2f}"


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def phase_check():
    say(f"== {company.name} (id {company.id})")
    say()

    cats = env["product.category"].search([("id", "child_of", _root_category().id)])
    modes = {}
    for cat in cats.with_company(company):
        modes.setdefault((cat.property_cost_method, cat.property_valuation), []).append(cat.name)
    say("Product categories")
    for (cost, valuation), names in sorted(modes.items()):
        say(f"   {cost:<9} {valuation:<10} {len(names):>3}  e.g. {names[0]}")
    odd = [c.name for c in cats.with_company(company) if c.property_cost_method != "average"]
    if odd:
        say(f"   !! not on average cost: {', '.join(odd)}")
    say()

    say("Locations (a journal entry needs an account on one side of the move)")
    for loc in Location.search([]):
        owner = loc.company_id.name or "SHARED"
        mark = loc.valuation_account_id.code or "-"
        flag = "  <= shared, do not set" if not loc.company_id else ""
        say(f"   {loc.id:>3} {loc.complete_name:<34} {loc.usage:<10} {owner:<24} {mark}{flag}")
    say()

    bills = env["account.move"].search(
        [("company_id", "=", company.id), ("move_type", "in", ("in_invoice", "in_refund"))]
    )
    drafts = bills.filtered(lambda b: b.state == "draft")
    zero_lines = bills.invoice_line_ids.filtered(
        lambda line: line.display_type == "product" and not line.price_unit
    )
    say(f"Vendor bills: {len(bills)} total, {len(drafts)} draft, "
        f"{len(bills.filtered(lambda b: b.payment_state in ('paid', 'in_payment')))} paid")
    say(f"   lines priced at zero: {len(zero_lines)}")
    if drafts:
        say(f"   !! {len(drafts)} draft bills worth {money(sum(drafts.mapped('amount_total')))} "
            f"must be priced and posted BEFORE the flip, or the opening stock is understated")
    say()

    products = env["product.product"].search(
        [("company_id", "in", (False, company.id)), ("bar_desk_ok", "=", True)]
    ).with_company(company)
    nil = products.filtered(lambda p: not p.standard_price)
    say(f"Desk products: {len(products)}, {len(nil)} with no cost")
    for product in nil:
        say(f"   - {product.display_name}")
    say()

    kits = env["mrp.bom"].search([("company_id", "in", (False, company.id)), ("type", "=", "phantom")])
    say(f"Kits (bottle service, glass pours, cocktails): {len(kits)} phantom BoMs")
    say()

    lines = env["sale.order.line"].search(
        [("company_id", "=", company.id), ("order_id.state", "in", ("sale", "done"))]
    )
    costless = lines.filtered(lambda line: not line.purchase_price and line.move_ids)
    say(f"Sale lines: {len(lines)}, {len(costless)} with stock moves but no cost recorded")
    say("   (lines with no stock moves -- services, rounding -- correctly stay at zero)")


def _root_category():
    root = env["product.category"].search(
        [("name", "=", "Dhostana Bar Stock"), ("parent_id", "=", False)], limit=1
    )
    if not root:
        raise SystemExit("Root category 'Dhostana Bar Stock' not found")
    return root


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def phase_config():
    drafts = env["account.move"].search_count(
        [("company_id", "=", company.id), ("move_type", "=", "in_invoice"), ("state", "=", "draft")]
    )
    if drafts:
        say(f"!! {drafts} vendor bills are still in draft. Price and post them first:")
        say("   their stock is already on the shelf but its value is not in the books,")
        say("   so the opening position would be short by exactly their total.")
        if APPLY:
            raise SystemExit("Refusing to arm valuation with draft bills outstanding.")

    shared_vendors = env.ref("stock.stock_location_suppliers")
    vendors = Location.search(
        [("company_id", "=", company.id), ("usage", "=", "supplier")], limit=1
    )
    if not vendors:
        say(f"CREATE location 'Vendors/{company.name}' (usage supplier, company {company.id})")
        say("   -- the shared Vendors location must stay untouched: valuation_account_id")
        say("      is not company-dependent, so an account there would arm every company")
        if APPLY:
            vendors = Location.create({
                "name": company.name,
                "usage": "supplier",
                "location_id": shared_vendors.id,
                "company_id": company.id,
            })
    else:
        say(f"location {vendors.id} {vendors.complete_name} already exists")

    wanted = {}
    if vendors:
        wanted[vendors] = account(ACCOUNTS["interim_received"])
    for loc in Location.search([("company_id", "=", company.id), ("usage", "=", "inventory")]):
        key = "scrap" if "scrap" in loc.name.lower() else "variance"
        wanted[loc] = account(ACCOUNTS[key])
    for loc in Location.search([("company_id", "=", company.id), ("usage", "=", "customer")]):
        if loc.id in _pos_sale_location_ids():
            say(f"SKIP  {loc.complete_name}: POS sales already post cost of sales through the "
                f"invoice; an account here would book it a second time")
            continue
        wanted[loc] = account(ACCOUNTS["given_away"])

    for loc, acc in wanted.items():
        if loc.valuation_account_id == acc:
            say(f"ok    {loc.complete_name} -> {acc.code}")
            continue
        say(f"SET   {loc.complete_name} -> {acc.code} {acc.name}")
        if APPLY:
            loc.valuation_account_id = acc

    # Described whether or not the location exists yet, so a dry run shows
    # everything APPLY=1 would do rather than only the first step.
    target = vendors.complete_name if vendors else f"Vendors/{company.name}"
    suppliers = env["res.partner"].search([("supplier_rank", ">", 0)])
    moving = suppliers.with_company(company).filtered(
        lambda p: not vendors or p.property_stock_supplier != vendors
    )
    say(f"POINT {len(moving)} suppliers' stock location at {target}")
    if APPLY and vendors and moving:
        moving.with_company(company).property_stock_supplier = vendors
    for bar in env["odin.bar"].search([("company_id", "=", company.id), ("kind", "=", "store")]):
        kind = bar.receipt_type_id
        if kind and (not vendors or kind.default_location_src_id != vendors):
            say(f"SET   {kind.display_name}.default_location_src_id -> {target}")
            if APPLY and vendors:
                kind.default_location_src_id = vendors

    # Company first, then the root, then the children: no window in which a
    # child inherits 'periodic' from a company that is already 'real_time'.
    say("FLIP  company and categories to Perpetual (at invoicing), average cost")
    root = _root_category()
    cats = env["product.category"].search([("id", "child_of", root.id)])
    if APPLY:
        company.write({"inventory_valuation": "real_time", "cost_method": "average"})
        for cat in [root] + [c for c in cats if c != root]:
            cat.with_company(company).write({
                "property_valuation": "real_time",
                "property_cost_method": "average",
            })
    for cat in cats.with_company(company):
        if cat.property_cost_method != "average":
            say(f"   {cat.name}: cost method {cat.property_cost_method} -> average")


def _pos_sale_location_ids():
    """Where POS sales are delivered. Odoo posts cost of sales for those from
    the customer invoice, so valuing the move as well would double it."""
    types = env["odin.bar"].search([("company_id", "=", company.id)]).sale_type_id
    return set(types.default_location_dest_id.ids)


# ---------------------------------------------------------------------------
# costs
# ---------------------------------------------------------------------------

def phase_costs():
    products = env["product.product"].search(
        [("company_id", "in", (False, company.id)), ("bar_desk_ok", "=", True)]
    ).with_company(company)

    say("Recompute cost from purchase history (average of what was actually paid)")
    say("   this must run BEFORE any cost is typed by hand: writing a cost")
    say("   anchors the average and every earlier receipt is ignored from then on")
    if APPLY:
        products._update_standard_price()

    still_nil = products.filtered(lambda p: not p.standard_price)
    say(f"   {len(products) - len(still_nil)} priced from history, {len(still_nil)} still at zero")
    for product in still_nil:
        say(f"   - {product.display_name}  (no priced receipt exists; needs a figure from the price list)")

    kits = env["mrp.bom"].search(
        [("company_id", "in", (False, company.id)), ("type", "=", "phantom")]
    )
    say(f"Roll {len(kits)} kit costs up from their components")
    say("   bottle service, glass pours and cocktails are all phantom BoMs, so their")
    say("   cost follows the tot they are poured from. A service such as corkage has")
    say("   no cost to the business and is left at zero.")
    if APPLY:
        templates = kits.product_tmpl_id.with_company(company)
        if hasattr(templates, "button_bom_cost"):
            templates.button_bom_cost()
        else:
            say("   !! mrp_account is not installed: no BoM cost rollup available")


# ---------------------------------------------------------------------------
# restate
# ---------------------------------------------------------------------------

def phase_restate():
    # is_out is stored, so this is a search rather than loading every move.
    moves = env["stock.move"].search([
        ("company_id", "=", company.id), ("state", "=", "done"), ("is_out", "=", True),
    ])
    say(f"Restate {len(moves)} outgoing moves at the costs now on the products")
    say("   note: Odoo restates these at the final blended average, not a")
    say("   per-day replay. For a few months of trading that is the right")
    say("   trade, but it cannot be undone by running this again.")
    if APPLY:
        moves._set_value()

    lines = env["sale.order.line"].search([
        ("company_id", "=", company.id), ("order_id.state", "in", ("sale", "done")),
    ])
    say(f"Recompute cost and margin on {len(lines)} sale lines")
    say("   sale_stock_margin already depends on move value, so margin follows")
    if APPLY:
        locked = lines.order_id.filtered("locked")
        if locked:
            locked.action_unlock()
        env.add_to_compute(lines._fields["purchase_price"], lines)
        lines.flush_recordset()
        if locked:
            locked.action_lock()
    costless = lines.filtered(lambda line: not line.purchase_price and line.move_ids)
    say(f"   {len(costless)} lines still carry stock but no cost")


# ---------------------------------------------------------------------------
# opening
# ---------------------------------------------------------------------------

def phase_opening():
    """Print the figures the opening entry needs. Posting it is the
    accountant's, not this script's."""
    products = env["product.product"].search(
        [("company_id", "in", (False, company.id)), ("is_storable", "=", True)]
    ).with_company(company)
    total = 0.0
    rows = []
    for product in products:
        value = product.total_value if "total_value" in product._fields else (
            product.qty_available * product.standard_price
        )
        if value:
            rows.append((product.display_name, product.qty_available, product.standard_price, value))
            total += value
    rows.sort(key=lambda row: -row[3])
    say("Opening stock value, for the entry the accountant posts:")
    say(f"   DR {account('100100').code} Stock      {money(total)}")
    say(f"   CR opening balance equity     {money(total)}")
    say()
    say("   Largest holdings:")
    for name, qty, cost, value in rows[:15]:
        say(f"     {name:<44} {qty:>9,.2f} x {cost:>9,.2f} = {money(value):>14}")
    say(f"   ... {len(rows)} products with value in total")
    say()
    say("   Cross-check this against Bar Control's stock value report before posting.")


PHASES = {
    "check": phase_check,
    "config": phase_config,
    "costs": phase_costs,
    "restate": phase_restate,
    "opening": phase_opening,
}

if PHASE not in PHASES:
    raise SystemExit(f"PHASE must be one of {', '.join(PHASES)}")

say(f"--- phase {PHASE}, {'APPLYING' if APPLY else 'dry run (set APPLY=1 to write)'} ---")
say()
PHASES[PHASE]()
say()
if APPLY:
    env.cr.commit()
    say("committed.")
else:
    env.cr.rollback()
    say("rolled back; nothing was written.")
print("\n".join(log))
