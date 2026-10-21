"""Put a bar company's P&L right: cost of revenue is what was used up.

In periodic valuation, Odoo 19 books a supplier bill to Cost of sales as a
purchase and nothing more. Only a stock closing turns purchases into the
cost of what was sold: it compares the stock on hand, valued, with the stock
account in the books and posts the difference to Stock Variation. Without
one, the P&L reads as if the opening stock cost nothing and every bottle
bought this month had been drunk this month -- or, while bills sit in draft,
as if nothing cost anything at all.

Run from `odoo shell`, one phase at a time, reading the report each time:

    PHASE=check  odoo shell -c /etc/odoo/odoo.conf -d <db> --no-http \\
        --workers 0 --max-cron-threads 0 < tools/dhostana_pnl.py

Nothing is written unless APPLY=1.

    PHASE=check     the month so far as Odoo holds it, and as it should read
    PHASE=config    Stock Variation into Cost of Revenue; Odoo's own closing
                    job switched on
    PHASE=opening   the opening stock entry, created as a DRAFT for the
                    accountant to check and post (OPENING_ACCOUNT=<code> of
                    the equity account it comes from)
    PHASE=close     the first closing, today, posted; from then on Odoo's
                    job posts one every night

Order: check, config, set the costs still missing, post the draft vendor
bills, opening (and the accountant posts it), close. `close` refuses while
vendor bills are in draft or the opening stock is not in the books, because
either would put stock on the balance sheet that the P&L never paid for.

Variables: COMPANY (default Dhostana Ventures Ltd), FROM (first day of the
period, default the first of this month), OPENING_DATE (default the day
before FROM), OPENING_VALUE ("current", the default: what was on hand then
at today's costs, the same measure the closing uses, so costs set late --
and many were -- count at both ends; or "historical": as Odoo valued each
move at the time, zeros and all).

This is the periodic route. tools/dhostana_perpetual.py is the perpetual one;
use one or the other, not both.
"""

import os
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from odoo import Command, fields
from odoo.tools import float_round

APPLY = os.environ.get("APPLY") == "1"
PHASE = os.environ.get("PHASE", "check")
COMPANY = os.environ.get("COMPANY", "Dhostana Ventures Ltd")

company = env["res.company"].search([("name", "=", COMPANY)], limit=1)
if not company:
    raise SystemExit(f"No company named {COMPANY!r}")
env = env(context=dict(env.context, allowed_company_ids=[company.id]))
company = company.with_env(env)

today = fields.Date.context_today(env["res.users"])
FROM = fields.Date.to_date(os.environ.get("FROM")) if os.environ.get("FROM") else today.replace(day=1)
OPENING_DATE = (
    fields.Date.to_date(os.environ["OPENING_DATE"]) if os.environ.get("OPENING_DATE") else FROM - timedelta(days=1)
)
log = []


def say(line=""):
    log.append(str(line))


def money(value):
    return f"{float_round(value, precision_digits=2):>15,.2f}"


def stock_account():
    account = company.account_stock_valuation_id
    if not account:
        raise SystemExit("The company has no stock valuation account (Accounting settings).")
    return account


def balance(domain):
    groups = env["account.move.line"]._read_group(
        [("company_id", "=", company.id), ("parent_state", "=", "posted")] + domain, [], ["balance:sum"]
    )
    return groups[0][0] or 0.0


def products():
    """Storable products of the company, valued where Odoo values stock."""
    return (
        env["product.product"]
        .search([("is_storable", "=", True), ("company_id", "in", (False, company.id))])
        .with_company(company)
    )


def stock_value_at(day, how=None):
    """Stock on hand at the end of ``day``: at today's costs by default, the
    same measure the closing uses for today, so a cost corrected since then
    moves neither end of the period; or as Odoo valued it at the time."""
    end = datetime.combine(day, time.max)
    if (how or os.environ.get("OPENING_VALUE", "current")) == "historical":
        return sum(products().with_context(to_date=end).mapped("total_value"))
    total = 0.0
    for product in products()._with_valuation_context().with_context(to_date=end):
        total += product.qty_available * product.standard_price
    return total


def stock_value_now():
    return sum(products().mapped("total_value"))


def draft_bills(until=None):
    domain = [("company_id", "=", company.id), ("move_type", "in", ("in_invoice", "in_refund")), ("state", "=", "draft")]
    if until:
        domain.append(("invoice_date", "<=", until))
    return env["account.move"].search(domain, order="invoice_date, id")


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def phase_check():
    say(f"== {company.name}, {FROM} to {today}")
    say()
    account = stock_account()
    variation = account.account_stock_variation_id or company.expense_account_id
    say("Set-up")
    say(f"   stock account          {account.code} {account.name}")
    say(f"   stock variation        {variation.code} {variation.name}  ({variation.account_type})")
    if variation.account_type != "expense_direct_cost":
        say("   !! not in Cost of Revenue: the stock change would land below gross profit (PHASE=config)")
    say(f"   closing                {company.inventory_period}"
        + ("  !! never posted by itself (PHASE=config)" if company.inventory_period == "manual" else ""))
    valuations = defaultdict(int)
    for product in products():
        valuations[(product.valuation, product.cost_method)] += 1
    for (valuation, cost_method), count in sorted(valuations.items()):
        say(f"   {count:>4} products        {valuation}, {cost_method}")
    if any(valuation == "real_time" for valuation, _cost in valuations):
        say("   !! some products are perpetual: this script is for periodic valuation")
    say()

    in_period = [("date", ">=", FROM), ("date", "<=", today)]
    revenue = -balance(in_period + [("account_id.account_type", "in", ("income", "income_other"))])
    cost = balance(in_period + [("account_id.account_type", "=", "expense_direct_cost")])
    say("The P&L today")
    say(f"   revenue                {money(revenue)}")
    say(f"   cost of revenue        {money(cost)}")
    say(f"   gross profit           {money(revenue - cost)}   {100 * (revenue - cost) / revenue if revenue else 0:.0f}%")
    say()

    drafts = draft_bills()
    say(f"Vendor bills in draft: {len(drafts)} -- their stock is on the shelf, their cost is not in the books")
    for bill in drafts:
        lines = bill.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        unpriced = len(lines.filtered(lambda line: not line.price_unit))
        say(f"   {str(bill.invoice_date or ''):<10} {bill.partner_id.name[:22]:<22} {bill.ref or '':<16} "
            f"{money(bill.amount_total)}  {len(lines)} lines, {unpriced} without a price")
    say()

    opening = stock_value_at(OPENING_DATE)
    opening_books = balance([("account_id", "=", account.id), ("date", "<=", OPENING_DATE)])
    closing = stock_value_now()
    closing_books = balance([("account_id", "=", account.id)])
    purchases = balance(in_period + [("journal_id.type", "=", "purchase"), ("account_id.account_type", "=", "expense_direct_cost")])
    say("Stock")
    say(f"   on hand {OPENING_DATE}   {money(opening)}   in the books {money(opening_books)}")
    say(f"     (at the costs of the time: {money(stock_value_at(OPENING_DATE, 'historical'))})")
    say(f"   on hand today          {money(closing)}   in the books {money(closing_books)}")
    say()

    moves = env["stock.move"].search([
        ("company_id", "=", company.id), ("state", "=", "done"),
        ("date", ">=", datetime.combine(FROM, time.min)), ("date", "<=", datetime.combine(today, time.max)),
        "|", ("is_in", "=", True), ("is_out", "=", True),
    ])
    used = defaultdict(float)
    for move in moves:
        other = move.location_dest_id if move.is_out else move.location_id
        if other.usage == "supplier":
            continue
        used[other.complete_name] += move.value if move.is_out else -move.value
    say("Used up this period, as Odoo valued each move (sales, events, counts)")
    for name, value in sorted(used.items(), key=lambda item: -abs(item[1])):
        say(f"   {name[:30]:<30} {money(value)}")
    consumed = sum(used.values())
    say(f"   {'total':<30} {money(consumed)}")
    if revenue:
        say(f"   gross profit on it     {money(revenue - consumed)}   {100 * (revenue - consumed) / revenue:.0f}%")
    zero = moves.filtered(lambda move: move.is_out and not move.value)
    if zero:
        say(f"   !! {len(zero)} moves out valued at nothing: their products had no cost")
    say()

    say("Once the bills are posted, the opening entry is in and the closing has run:")
    say(f"   cost of revenue = purchases {money(purchases)} (posted so far)")
    say(f"                   + opening   {money(opening)}")
    say(f"                   - closing   {money(closing)}")
    say(f"                   + the bills still in draft, at their real prices")
    say()

    nil = products().filtered(lambda product: product.qty_available > 0 and not product.standard_price)
    say(f"On hand with no cost: {len(nil)} -- valued at nothing at both ends; set them before the opening entry")
    for product in nil:
        say(f"   - {product.display_name} ({product.qty_available:g} {product.uom_id.name})")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def phase_config():
    account = stock_account()
    variation = account.account_stock_variation_id
    if not variation:
        raise SystemExit(f"{account.code} has no stock variation account: set one on it first.")
    if variation.account_type != "expense_direct_cost":
        say(f"SET   {variation.code} {variation.name}: {variation.account_type} -> Cost of Revenue")
        if APPLY:
            variation.account_type = "expense_direct_cost"
    else:
        say(f"ok    {variation.code} {variation.name} is in Cost of Revenue")
    if account.account_stock_expense_id:
        # With a stock expense account as well, Odoo adds a "continental"
        # step to each closing that shuttles the year's change in stock
        # between it and Stock Variation. The total is unchanged but the
        # lines are not: Cost of sales swells by the opening stock and Stock
        # Variation goes negative, and the year end resets it. Without it a
        # closing is one plain entry: stock against Stock Variation.
        say(f"CLEAR {account.code}'s stock expense account ({account.account_stock_expense_id.code}): "
            "closings post stock against Stock Variation only")
        if APPLY:
            account.account_stock_expense_id = False
    if not company.account_stock_journal_id:
        raise SystemExit("Set the inventory valuation journal in the Accounting settings first.")
    cron = env.ref("stock_account.ir_cron_post_stock_valuation", raise_if_not_found=False)
    if not cron:
        raise SystemExit("Odoo's stock closing job is missing: update stock_account.")
    if not cron.active:
        say("SET   scheduled job 'Inventory Valuation Closing' -> active")
        if APPLY:
            cron.active = True
    else:
        say(f"ok    scheduled job '{cron.name}' runs every {cron.interval_number} {cron.interval_type}")
    # Left to PHASE=close: a nightly closing against books still missing the
    # opening stock or the draft bills would post exactly the wrong figure.
    say(f"      stock closing is {company.inventory_period}; PHASE=close makes it daily after the first one")


# ---------------------------------------------------------------------------
# opening
# ---------------------------------------------------------------------------

def phase_opening():
    account = stock_account()
    value = float_round(stock_value_at(OPENING_DATE), precision_digits=2)
    books = balance([("account_id", "=", account.id), ("date", "<=", OPENING_DATE)])
    amount = float_round(value - books, precision_digits=2)
    say(f"Stock on hand at the end of {OPENING_DATE}, at today's costs: {money(value)}")
    say(f"Already in {account.code} at that date:                     {money(books)}")
    nil = products().filtered(lambda product: product.qty_available > 0 and not product.standard_price)
    if nil:
        say(f"!! {len(nil)} products on hand have no cost and count for nothing here (PHASE=check lists them)")
    if company.currency_id.is_zero(amount):
        say("Nothing to post.")
        return
    code = os.environ.get("OPENING_ACCOUNT")
    equity = env["account.account"].search(
        [("company_ids", "in", company.id), ("code", "=", code)], limit=1
    ) if code else env["account.account"]
    say()
    say(f"   DR {account.code} {account.name:<28} {money(max(amount, 0))}")
    say(f"   CR {(equity.code or '<OPENING_ACCOUNT>')} {(equity.name or 'opening balance equity'):<28} {money(max(amount, 0))}")
    if amount < 0:
        say("   (reversed: the books hold more stock than was on hand)")
    if not equity:
        say()
        say("Equity accounts to take it from (OPENING_ACCOUNT=<code>):")
        for candidate in env["account.account"].search(
            [("company_ids", "in", company.id), ("account_type", "in", ("equity", "equity_unaffected"))]
        ):
            say(f"   {candidate.code} {candidate.name}")
        return
    if APPLY:
        entry = env["account.move"].create({
            "move_type": "entry",
            "journal_id": company.account_stock_journal_id.id,
            "date": OPENING_DATE,
            "ref": f"Opening stock at {OPENING_DATE}",
            "line_ids": [
                Command.create({"account_id": account.id, "name": "Opening stock",
                                "debit": max(amount, 0), "credit": max(-amount, 0)}),
                Command.create({"account_id": equity.id, "name": "Opening stock",
                                "debit": max(-amount, 0), "credit": max(amount, 0)}),
            ],
        })
        say(f"DRAFT {entry.display_name} created: the accountant checks and posts it.")


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------

def phase_close():
    account = stock_account()
    refuse = []
    drafts = draft_bills(until=today)
    if drafts:
        refuse.append(f"{len(drafts)} vendor bills are in draft: price and post them first")
    if stock_value_at(OPENING_DATE) and not balance([("account_id", "=", account.id), ("date", "<=", OPENING_DATE)]):
        refuse.append("the opening stock is not in the books: PHASE=opening, then post it")
    variation = account.account_stock_variation_id or company.expense_account_id
    if variation.account_type != "expense_direct_cost":
        refuse.append(f"{variation.code} is not in Cost of Revenue: PHASE=config")
    for reason in refuse:
        say(f"!! {reason}")
    lines = company._action_close_stock_valuation()
    say()
    say("The closing, today:")
    for line in lines:
        acc = env["account.account"].browse(line["account_id"])
        say(f"   {acc.code} {acc.name[:28]:<28} {money(line['debit'])} {money(line['credit'])}")
    if not lines:
        say("   nothing to post: the books already hold the stock on hand")
    if APPLY and refuse:
        raise SystemExit("Refusing to close: see above.")
    if APPLY and lines:
        company.action_close_stock_valuation(auto_post=True)
        say("posted.")
        if company.inventory_period == "manual":
            company.inventory_period = "daily"
            say("closing set to daily: Odoo's job posts it each night from now on.")


PHASES = {
    "check": phase_check,
    "config": phase_config,
    "opening": phase_opening,
    "close": phase_close,
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
