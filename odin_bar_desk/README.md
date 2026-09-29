# Bar Desk (`odin_bar_desk`)

A phone-first stock desk for bars, inside Odoo. Staff record stock outs,
count the bar blind at night and check deliveries on a shared tablet with a
PIN. The storekeeper sends stock to bars and books supplier deliveries.
Managers approve counts in the normal Odoo backend. Every record is a standard
Odoo transfer, scrap or inventory adjustment.

Odoo 19 Community. Depends on `odin_bar_base` and `hr`. Barcode scanning is
not part of this module (see *Not included*).

## Who sees what

| Who | Device | What they get |
|---|---|---|
| Bar staff | The bar's shared phone or tablet, logged in as the bar's Desk login | Only the Bar Desk, full screen, no Odoo menus. They sign in with name + PIN. |
| Storekeeper | The Main Store device | The same Desk in store mode: Send to bar, Receive, Count store, Disputes. |
| Manager | Their own Odoo login | The **Bar Control** app: Dashboard, Bar Desk (any bar), Count approvals, Stock-out log, Deliveries, POS import, Reporting. |

## Setting up

1. **Bars**: *Bar Control → Configuration → Bars*. The POS importer's upgrade
   has already created Banda Bar, Bulls Eye and Main Bar with their POS
   setup. Add the Main Store (kind Store, location `MS/Stock`), then press
   **Fill from stock setup** on each bar. The warning box lists anything still
   missing.
2. **Stock-out reasons**: created with the first bar and mapped to your `ROMA`,
   `EVT`, `DEBT` and `IBT` operation types and to the Breakage, Spoilage,
   Flat / Returned by guest and Expired scrap reasons. Check them under
   *Configuration → Stock-out reasons*.
3. **Products**: on each product's Inventory tab, tick **Show in Bar Desk** and
   set the **Count sheet order**, with the category order on the product
   category, so the count follows the paper sheet. For spirits kept in tots,
   **Counting bottle** is filled from the product's bottle packagings, e.g.
   Bottle 750ml (25 tots). Leave it empty for beer, sodas and wine, which are
   counted in units. Crate packagings (Crate of 24/25) are offered for
   quantities.
4. **Count sheet**: on the bar's *Bar Desk* tab, list the products this bar
   counts. Left empty, it counts every Desk product.
5. **Staff**: on each employee, set a **PIN** and the **Bar Desk bars** where
   they may sign in, or tick **All bars** for relief staff and supervisors.
6. **Desk logins**: on each bar, press **Create Desk login**, then set its
   password. The login opens straight on the Desk. A roving supervisor's phone
   can be allowed several bars (*Other Desk bars* on the user), which gives
   it a bar switcher.
7. **Managers**: give them *Bar Desk / Manager*. Bar tablets get *Bar Desk /
   Staff*, their only group.

## Training walkthrough

**Signing in (every shift change).** The tablet shows the bar and the staff
names. Tap your name, type your PIN, OK. Your name stays in the top corner:
tap it to hand over. After 10 minutes without a touch the Desk locks itself.

**Home.** Three tiles, **Stock out**, **Count** and **Deliveries**, the last
with a "not checked" badge. Below them, **Today**: everything that happened at
this bar today, who did it and when.

**Stock out** (anything leaving other than a sale)
1. Tap **Stock out**, then the reason: Roma, Event, Unpaid bill, Breakage,
   Spoiled, Flat/returned, Expired, Back to store, To another bar.
2. *Unpaid bill* asks for the member name or number. *To another bar* asks
   which bar.
3. **Add item**. The most-used items at this bar are on top; use a category
   chip or type a few letters to find others.
4. On the keypad, type the number and pick the unit: tots or a bottle size for
   spirits, units or a crate for beer. Use ±1 on a line to adjust.
5. **Done**. It is posted straight away and appears in Today.

*Training line: "Pick why it left, tap the items, Done."*

**Closing count** (every night)
1. Tap **Count → Closing count for Sun 27 Sep**. Before 06:00 it is still the
   previous day. *Count an earlier day* reaches the two days before.
2. The list follows the paper sheet, by category. Every line starts grey.
   No expected quantities are shown.
3. Spirits: type the **full bottles** (per size if the bar has both 750ml and
   1L), then the **open bottle** in tots.
4. Beer and sodas: **crates**, then **loose** bottles. Everything else: the
   count.
5. Finished items: tap and OK, an explicit 0. The line turns green.
6. It saves as you go, to the server and to the device. You can hand over,
   lock or lose the connection and carry on.
7. **Submit** works once every line is green. The count then goes to a
   manager.

A **spot count** takes a few items at any time. It adjusts only those items,
and only the ones the POS did not sell at the bar that day (see *Rules*).

*Training line: "Count every night: full bottles, then tots in the open one."*

**Deliveries** (optional check at the bar)
1. **Deliveries** lists what came in over two weeks: *Not checked*,
   *Confirmed* or *Disputed*.
2. Open one. Lines show what was sent.
3. If everything matches, tap **All correct**.
4. If not, tap a line and enter what arrived (the line turns orange), then
   **Report difference**.
5. No time? Skip it. The stock is at the bar the moment it was sent.

*Rule for staff: "If you don't check a delivery, any shortage in it counts
against your bar."*

**Store mode** (storekeeper)
- **Send to bar**: pick the bar, add items (crates, bottles), then **Send to
  Bulls Eye**. It is validated at once and shows at the bar as *Not checked*.
- **Receive**: a waiting purchase-order receipt, with ordered quantities
  filled in, or **Delivery from a supplier** (pick the supplier, add items,
  Done). Short receipts leave a backorder.
- **Count store**: the same count flow, on `MS/Stock`.
- **Disputes**: bars' reported differences. **Accept** when the goods never
  left the store, which moves them back. **Reject** leaves the variance with
  the bar.

*Training line: "Store: Send to bar, pick the bar, add items, Send."*

**Manager: every morning**
1. *Bar Control → POS import → Import a day*: drop yesterday's Group Sales
   Register PDF, check the review, **Post day**.
2. The posted day lists **Bar counts ready to approve**. Click one, check it,
   **Approve**. Done.

*Training line: "Post yesterday's POS, then approve last night's counts."*

**Manager**
- **Dashboard**: per bar, deliveries not checked, counts to approve, stock outs
  today, open disputes, last approved close, last POS day, *POS day missing*
  and 7-day variance.
- **Count approvals**: open a count to see counted, expected, difference and
  value. **Approve** stays disabled with "Post the POS import for Bulls Eye on
  Sun 27 Sep before approving this count." until the POS sales of that day,
  and of every day before it, are posted. **Open POS import** goes straight to
  that day's import. Counts are approved in the order they were taken. **Ask
  for a recount** shows "Recount requested" on the bar's Desk.
- **Deliveries → Disputes** holds transfer disputes between bars, decided here:
  validate the draft, or cancel it.
- **Reporting → Variance** shows posted differences by product and bar.
  **Bar Desk log** is every Desk action, wrong PINs included.

## Rules the server enforces

- **Blind counts.** Staff logins cannot read stock levels: record rules hide
  quants, move lines and the stock report from them, on top of having no stock
  rights. The Desk never sends expected quantities.
- **Bar lock.** A staff login acts only for its own bar or bars, whatever the
  client sends. Every Desk call also checks a signed staff token issued at PIN
  sign-in. The token is valid for 16 hours for that login, bar and person.
  Five wrong PINs in five minutes lock that person out for a while.
- **Approval posts the difference, not the count.** Expected is the stock as
  it stood when the count was submitted, including that day's POS sales even
  when they are posted later. The adjustment is dated at the count, so the
  next morning's delivery is kept and later counts stay right whatever order
  they are approved in.
- **Counts wait for the POS import** of their day and of every day before it,
  from the bar's first POS day (*POS days from* on the bar). A day imported
  late would otherwise change the stock under a count already approved.
  Earlier counts of the same bar must be approved or cancelled first.
- **Spot counts leave alone what sold that day.** POS sales come as one total
  per day, so a mid-shift count cannot tell how many were sold before it.
  Items the POS sold at the bar that day are marked *Sold that day* and not
  adjusted; the closing count checks them. Everything else is adjusted.
- **An undone POS day reopens its counts.** Undoing a POS import sends every
  count of that bar approved for that day or later back for approval, with
  its adjustment reversed. After the corrected import, approve them again:
  the variance is worked out on the new sales. The import screen lists these
  counts before you undo.
- **One approved closing count per bar and day**, enforced by a database index.
  A new closing count replaces one still waiting for approval.
- **Retries are safe.** Every Desk action carries a request id with a unique
  index. If the connection drops, the device keeps the action and sends it
  again; the server posts it once.

## How Desk actions land in Odoo

| Desk action | Odoo record |
|---|---|
| Roma / Event / Unpaid bill | Transfer of the reason's type (`ROMA` / `EVT` / `DEBT`) from the bar, validated |
| Breakage / Spoiled / Flat / Expired | Scrap from the bar with the scrap reason |
| Back to store | Bar's `RET-xx` transfer to the store, validated |
| To another bar | `IBT` transfer to that bar, validated. Shows in its deliveries |
| Send to bar (store) | Bar's `ISS-xx` transfer, validated |
| Receive (store) | Receipt into the store, validated |
| Delivery disputed | Draft return (short) or draft transfer (extra), linked to the delivery |
| Count approved | Inventory adjustment moves for the differences, linked to the count |

Every record carries the staff member (`bar_employee_id`) and the Desk action
(`bar_activity_id`). Deliveries carry `bar_ack_state`, who checked them and
when.

## Open points

- **Sales after midnight.** The trading day ends at 06:00, so a count at
  01:30 closes the day before. This matches the POS report only if the POS
  puts a sale rung up at 00:30 on the report of the day before. If the POS
  report runs midnight to midnight, those sales land on the next day's report
  and show as a shortage on one night and a surplus on the next. Check one
  late night's report with the club before go-live.
- **Barcode scanning** is left out on purpose. It can be added later as a
  separate module that feeds scanned products into the same keypad and lines.

## Tests

```
odoo-bin -d <db> -i odin_bar_desk --test-tags /odin_bar_desk --stop-after-init
```

These cover count approval with a next-morning delivery, the POS import posted
before or after a count, counts waiting for every earlier POS day, an undone
POS day reopening its counts, spot counts leaving items sold that day, retries
with the same request id, staff refused for another bar, blind counting,
disputes, store mode, and two browser tours on the Desk. The tours need Chrome or
Chromium; set `ODOO_BROWSER_BIN` if it is not on the path.
