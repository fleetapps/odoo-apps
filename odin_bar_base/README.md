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
| POS customer, POS group name, POS suffix | used by the POS importer |
| POS import required | closing counts wait for the day's POS import |
| Timezone, trading day start | Africa/Nairobi, 06:00 |

**Fill from stock setup** fills empty fields from the names already in use:
`ISS-<code>`, `RET-<code>` and `SAL-<code>`, the store's receipt type, and an
analytic account named like the bar.

**POS days (`odin.bar.pos.day`)**, under *Bar Control → POS import*. There is
one row per bar and trading day whose POS sales are posted to stock. A
manager can add a day by hand, with "No sales", when a bar did not trade.

**Trading day on transfers.** `stock.picking.bar_business_date` is the
business day a transfer belongs to.

## Trading day

A trading day runs from the bar's start hour (06:00) to the same time the
next day, in the bar's timezone. A count at 01:30 on Sunday belongs to
Saturday.

## Contract for the POS importer

The importer posts the day's sales after the day has ended. For each bar and
day it must:

1. Create the sales delivery out of the bar, as today via a sale order for
   the bar's POS customer and the `SAL-xx` type.
2. Set `bar_business_date` to the trading day on that delivery.
3. Call `bar._mark_pos_posted(day, source="<import reference>")` once the
   delivery is validated. Calling it again for the same day just updates the
   reference.

`bar._pos_day_posted(day)` tells whether step 3 happened. Stores, and bars with
*POS import required* off, always count as posted.

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
