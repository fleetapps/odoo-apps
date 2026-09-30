# Bar Base (`odin_bar_base`)

One definition of "a bar", shared by the Bar Desk and the POS importer.

Odoo 19 Community. Depends on `stock` and `analytic`.

## What it adds

**Bars (`odin.bar`)**, under *Bar Control → Configuration → Bars*. Each record
ties together:

| Field | Example (Dhostana) |
|---|---|
| Name, code | Bulls Eye, `BE` |
| Kind | Bar, or Store for the Main Store |
| Stock location | `MS/Bulls Eye (BE)` |
| Store | Main Store (where it is issued from and returns to) |
| Issue / Return / POS sales types | `ISS-BE`, `RET-BE`, `SAL-BE` |
| Receipt type (stores) | `IN` |
| Analytic account | Bulls Eye (BE) |
| POS group name, POS suffix, POS delivery contact | Bulls Eye, `BE`, the club's Bulls Eye contact (used by the POS importer) |
| POS import required, POS days from | counts wait for the POS days; the first imported day |
| Timezone, trading day start | Africa/Nairobi, 06:00 |

**Fill from stock setup** fills empty fields from the names already in use:
`ISS-<code>`, `RET-<code>` and `SAL-<code>`, the store's receipt type, and an
analytic account named like the bar.

**POS days (`odin.bar.pos.day`)**, under *Bar Control → POS import → Days by
bar*. There is one row per bar and trading day whose POS sales are posted to
stock. The POS importer writes them; a manager adds one by hand only for a day
without any POS report, for instance when the club was closed.

**Trading day on transfers.** `stock.picking.bar_business_date` is the
business day a transfer belongs to.

## Trading day

A trading day runs from the bar's start hour (06:00) to the same time the
next day, in the bar's timezone. A count at 01:30 on Sunday belongs to
Saturday.

## Stock and POS sales

Stock is the one place quantities live: the POS importer takes each day's
sales off the bar locations, and counts compare against those same
locations. Two facts about POS sales are not in stock, so the importer
records them next to the stock moves, in the same transaction:

- **Which trading day a sale belongs to.** Sales arrive the morning after, as
  one total per bar, so the delivery carries `bar_business_date`.
- **Which days are in.** Stock cannot tell "not imported yet" from "sold
  nothing", so each posted day gets a POS day row per bar.

## Contract for the POS importer

When it posts a day, the importer:

1. Creates the sales delivery out of each bar that sold something, with the
   bar's `SAL-xx` type, and sets `bar_business_date` to the trading day.
2. Calls `bar._mark_pos_posted(day, source="<import reference>")` for every
   bar of the report, with `no_sales=True` for a bar the report has nothing
   for.

When it undoes a day, it returns the deliveries with the same
`bar_business_date`, then calls `bar._unmark_pos_posted(day)`.

`gymkhana_pos_import` does all of this. What follows from it:

- `bar._pos_missing_day(day)` is the first day up to `day` whose POS sales
  are not posted, checked from the bar's *POS days from* date, which the first
  import sets. `bar._pos_day_posted(day)` is true when none is missing. Counts
  wait for this: a day imported late would change the stock under a count
  already approved. Stores, and bars with *POS import required* off, are never
  missing a day.
- Posting or undoing sales calls `bar._pos_day_changed(day)`. The Bar Desk
  uses it to send counts approved on the old figures back for approval.
- `bar._pos_moved_qty(products, day)` is what left the bar through the day's
  POS transfers, net of returns, kits as their components.
- `_pos_day_waiting(day)`, `_pos_day_dependents(day)` and
  `_pos_import_action(day)` let the importer's screen show counts it
  unblocks or would reopen, and let a waiting count open the import.

## Stock as it stood (`odin.bar._stock_as_of`)

`bar._stock_as_of(products, cutoff, business_date, closing=True)` returns each
product's quantity at the bar as it stood at `cutoff` (UTC) during
`business_date`. The Bar Desk approves counts with it.

- Transfers **without** a trading day count if they were done by `cutoff`.
- Transfers **with** a trading day (POS sales) count by that day, not by when
  they were posted:
  - earlier days always count;
  - the count's own day counts in full for a closing count, and only up to
    `cutoff` for a spot count;
  - later days never count.

This is why a count stays right whether the POS import ran before or after
it, and why the next morning's delivery is not part of the night's expected
stock.

## Tests

```
odoo-bin -d <db> -i odin_bar_base --test-tags /odin_bar_base --stop-after-init
```
