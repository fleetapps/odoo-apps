# Bar Desk (`odin_bar_desk`)

A phone-first stock desk for a club's bars and store, inside Odoo, run by
one stock controller. They log the day's moves between locations from the
paper sheet, count every location the next morning against the stock
expected, give each difference a reason, and approve the day. Supplier
deliveries are booked at the store against the invoice. Every record is a
standard Odoo transfer or inventory adjustment, and Bar Control reports stock
per location and its history.

Odoo 19 Community. Depends on `odin_bar_base`, `hr` and `purchase_stock`.

## The daily cycle

> Yesterday's count + stock moved in − stock moved out − POS sales
> = the stock expected this morning. Counted − expected = the difference to explain.

Each morning the controller closes the day before on the Desk's **day
sheet**, in five steps that each show whether they are done:

1. **POS sales**: yesterday's POS report imported for every bar (*Bar
   Control → POS import*; the Desk shows which are missing and, for a
   manager, opens the import).
2. **Moves**: what the paper sheet says moved between the store and the
   bars, or out of the club.
3. **Counts**: every location, each line showing what is expected.
4. **Differences**: a reason for each.
5. **Approve**: the differences are posted to stock under their reasons.

## Who sees what

| Who | Where | What they get |
|---|---|---|
| Stock controller (manager) | Their own Odoo login, *Bar Control → Bar Desk*, on a phone or laptop | The day sheet for every location: moves, counts, differences, approval, supplier deliveries, stock levels. |
| Staff on a shared tablet (optional) | The tablet's Desk login, full screen | The same Desk after name + PIN, for the locations they work at. They cannot approve. |
| Manager | The **Bar Control** app | Dashboard, Bar Desk, Count approvals, Moves, POS import, Reporting (stock by location, all locations, history, variance, Desk log), Configuration. |

## Setting up

1. **Bars**: *Bar Control → Configuration → Bars*. The POS importer's upgrade
   has already created Banda Bar, Bulls Eye and Main Bar with their POS
   setup. Add the Main Store (kind Store, location `MS/Stock`), then press
   **Fill from stock setup** on each bar. The warning box lists anything still
   missing.
2. **Moves out of the club and variance reasons**: created with the first
   bar. *Configuration → Moves out of the club* maps Roma, Event and Unpaid
   bill to your `ROMA`, `EVT` and `DEBT` operation types (and bar-to-bar moves
   to `IBT`). *Configuration → Variance reasons* holds Breakage, Spillage,
   Complimentary, Staff drink, Missed move, Counting mistake, POS error and
   Unexplained.
3. **Products**: on each product's Inventory tab, tick **Show in Bar Desk** and
   set the **Count sheet order**, with the category order on the product
   category, so the count follows the paper sheet. For spirits kept in tots,
   **Counting bottle** is filled from the product's bottle packagings, e.g.
   Bottle 750ml (25 tots). Leave it empty for beer, sodas and wine, which are
   counted in units. Crate packagings (Crate of 24/25) are offered for
   quantities. Reports and par levels read spirits in that bottle and beer
   and sodas in the crate (*Bulk unit*).
4. **Count sheet**: on the location's *Bar Desk* tab, list the products it
   counts. Left empty, it counts every Desk product.
5. **Stock controllers**: give their Odoo users *Bar Desk / Manager*: they
   open *Bar Control → Bar Desk*, sign in as themselves, and can approve the
   day. Link each user to an employee (the name shown on every move and
   count).
6. **A shared tablet (optional)**: on the Main Store, press **Create Desk
   login**, set its password, and list the bars under *Other Desk bars* on
   the user. Staff sign in on it with name and PIN: on each employee set a
   **PIN** and tick **All bars** (or list the locations they work at). Such
   staff log moves and count, but only a manager approves.
7. **Suppliers**: on each supplier's *Sales & Purchase* tab, **Supplies** is
   what the Desk shows on its tile (e.g. Beers and spirits) and **Supplies
   product categories** the products listed first when booking its delivery.
   Installing the module fills both for Dhostana's suppliers (Khetias, Soys,
   Mega Wines, Rosso Bianco, Siddham Wines, Sky City, Highridge, Benchmark,
   Muji, Geeta, Ishano, Outlook Index, Tony West, Rwathia).
8. **Paying suppliers**: on the Main Store's *Bar Desk* tab, set **Suppliers
   are paid from** to the cash or M-Pesa journal that pays deliveries.
9. **Par levels**: reorder rules (*Inventory → Operations → Replenishment*,
   trigger *Manual*) on `MS/Stock`. The Desk's **Stock levels** compares the
   club's total to them.

## Training walkthrough

**Signing in.** *Bar Control → Bar Desk*, tap your name, **Continue as me**
(on a shared tablet: your PIN). One sign-in covers every location. After 10
minutes without a touch the Desk locks itself.

**The day sheet.** It opens on yesterday (*Closing Sun 27 Sep*); the chips
on top reach the three days before and today. A tick marks an approved day.

**1. POS sales.** A chip per bar: green when the day's POS sales are in.
Counting does not wait for them, but approval does.

**2. Log a move** (from the paper sheet)
1. **From**: Main Store, Bulls Eye, Banda Bar or Main Bar. **To**: another
   location, or *Out of the club*: Roma, Event, Unpaid bill (asks for the
   member).
2. **When**: *Sun 27 Sep (before the count)*, the default while yesterday is
   open: the counts expect it. *Today (after the count)* for stock that moved
   after the locations were counted.
3. **Add item**, the quantity on the keypad (bottles for spirits, crates for
   beer and sodas, or tots and units), **Save move**. It is posted at once.

*Training line: "From, to, items, Save."*

**3. Counts.** Tap a location card.
1. The list follows the paper sheet. Each line shows **Expected 182 tots ·
   had 125 · moved +66 · sold 9**: the last count, what moved in or out since,
   what the POS sold.
2. When it matches, tap **Same**. Otherwise tap the line and type what is
   there: spirits as full bottles (per size) then the tots left in the open
   bottle; beer as crates then loose bottles. **+** adds up what sits in
   different places: `30+35+33`. The difference shows on the line at once.
3. **Differ (n)** lists what differs; **Add item not on the sheet** for
   anything else found.
4. It saves as you go. **Finish count** works once every line is entered;
   when something differs it goes straight to the differences.

*Training line: "Same when it matches, count it when it doesn't."*

**4. Differences.** Every line that differs, with its value. Tap one and
pick why:
- **Breakage, Spillage, Complimentary, Staff drink, POS error, Unexplained**:
  recorded (with an optional note) and posted under that reason at approval.
  Nobody is charged.
- **Missed move**: opens *Log a move* filled in, for the transfer nobody
  wrote down.
- **Counting mistake**: the keypad, to correct the count.

When one location is short by exactly what another has too much of (a crate
of Tusker gone from Bulls Eye, a crate too many at Main Bar), the screen
suggests the missed move: **Record BE → MB** fixes both lines.

**5. Approve.** Enabled once the POS sales are in, every location is
counted and every difference has a reason. Any manager can approve. The
differences are posted to stock, dated when each location was counted.

**Supplier delivery** (at the store), with the supplier's invoice in hand:
1. **New supplier delivery**, then the supplier. Each shows what it supplies.
2. Optional: **Add a photo of the invoice**. It is kept on the bill.
3. **What's on the invoice**: each item and its quantity (crates, bottles).
4. **What arrived?** Filled in from the invoice: change only what differs.
5. If something is missing, **The supplier will**: *Bring them later* (it
   stays under *Expected from suppliers*) or *Send a credit note*.
6. Invoice number (optional) and **Invoice total**, then **Paid now** (from
   the store's payment account) or **Pay later**.

**Stock levels.** Every product's club total in bottles or crates, where it
sits, and its par; **Below par** lists what to reorder. This replaces the
TOTALS and BULK AND REORDER sheets.

**Manager, in Bar Control**
- **Dashboard**: per location, counts to approve, differences to explain,
  moves today, last approved close, last POS day, *POS day missing*, 7-day
  variance.
- **Count approvals**: one count at a time, with counted, expected,
  difference, value and reason (editable while waiting). **Ask for a
  recount** marks it on the Desk.
- **Moves**: every move logged on the Desk.
- **Reporting**:
  - **Stock by location**: on hand now, grouped by location, in bottles or
    crates and in tots or units, with the value at cost.
  - **Stock, all locations**: every product side by side at each location
    and in total (switch the measure to *On hand* or *Value*).
  - **Stock history**: every movement in or out of each location: POS
    sales, moves, supplier deliveries, count adjustments, with the trading
    day, who logged it and the variance reason. Filter a product and a
    location to follow it day by day.
  - **Variance**: approved differences by reason, location, product and who
    counted; *Tapped Same* and *Corrected* filters show lines accepted
    without typing or fixed after the count.
  - **Bar Desk log**: every Desk action, wrong PINs included.

## Rules the server enforces

- **Expected stock.** A closing count of a day expects the stock as it stood
  when the location was counted: the previous count, moves logged for that
  day (whenever they were logged), anything else done before the count
  (a supplier delivery), less the POS sales of the day (even when imported
  later). Moves logged on *today* after a location was counted do not change
  its count.
- **Every difference has a reason** before approval. A move logged for the
  day, or a corrected line, is taken into account at once.
- **Logins and staff.** A shared login acts only for its own locations,
  whatever the client sends; staff only where they work. Every call checks a
  signed token issued at sign-in, valid 16 hours for that login, company and
  person. Five wrong PINs in five minutes lock that person out for a while.
  A shared Desk login cannot browse stock in Odoo.
- **No moves into an approved day.** A move for a day already approved at
  either location is refused; log it on today.
- **Approval posts the difference, not the count.** Expected is the stock as
  it stood when the count was submitted, including that day's POS sales even
  when they are posted later. The adjustment is dated at the count, so stock
  that moved after the count is kept and later counts stay right.
- **Counts wait for the POS import** of their day and of every day before it,
  from the bar's first POS day (*POS days from* on the bar). A day imported
  late would otherwise change the stock under a count already approved.
  Earlier counts of the same bar must be approved or cancelled first.
- **An undone POS day reopens its counts.** Undoing a POS import sends every
  count of that bar approved for that day or later back for approval, with
  its adjustment reversed. After the corrected import, approve them again:
  the variance is worked out on the new sales. The import screen lists these
  counts before you undo.
- **One approved closing count per location and day**, enforced by a
  database index. Counting again replaces a count still waiting.
- **Retries are safe.** Every Desk action carries a request id with a unique
  index. If the connection drops, the device keeps the action and sends it
  again; the server posts it once.

## How Desk actions land in Odoo

| Desk action | Odoo record |
|---|---|
| Move store → bar | The bar's `ISS-xx` transfer, validated |
| Move bar → store | The bar's `RET-xx` transfer, validated |
| Move bar → bar | `IBT` transfer, validated |
| Move out of the club | Transfer of the destination's type (`ROMA` / `EVT` / `DEBT`), validated |
| Supplier delivery (store) | Receipt into the store for what arrived, validated; supplier bill for what the invoice lists, paid from the store's journal when *Paid now*; an expected receipt (bring later) or a draft credit note (credit) for what was missing; the invoice photo attached |
| Count approved | Inventory adjustment moves for the differences, linked to the count and its variance reason |

Every move carries *Moved by hand* (`bar_manual_move`), the trading day it
was logged for (`bar_business_date`), who logged it (`bar_employee_id`) and
the Desk action (`bar_activity_id`). Moves logged by hand are never counted
as POS sales.

## Open points

- **Sales after midnight.** The trading day ends at 06:00, so a sale at
  01:30 belongs to the day before. This matches the POS report only if the POS
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

These cover the expected-stock breakdown (last count, moves, POS sales), a
morning move logged before a count, reasons required before approval,
missed-move suggestions and count corrections, approving a whole day, stock
levels and par, the two reports, counts approved in order and waiting for
every earlier POS day, an undone POS day reopening its counts, retries with
the same request id, logins and staff limited to their locations, supplier
deliveries, and two browser tours on the Desk (logging a move; counting and
explaining). The tours need Chrome or Chromium; set `ODOO_BROWSER_BIN` if it
is not on the path.
