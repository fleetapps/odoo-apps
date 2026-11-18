# Stock Book

The store's MOVEMENT LOG and MAIN INVENTORY sheets, inside Odoo 19, on one page
with a tab per month. Named *Stock Book* so it sits beside Odoo's Inventory app.

## Movements

* **Type in the last row**: date (`3`, `3/11`, `3 Nov`), item (any words of the
  code or name: `lush 500`), added or removed, reason, customer or supplier.
  Enter moves along; the item remembers the reason it is usually logged with.
  A reason that only removes (Sale, Spoiled...) or only adds takes the number
  in its own column, whichever one it was typed in.
* **Paste rows from Excel** into the last row's date or item cell: one draft
  per row. With a heading row (DATE, CODE or ITEM, ADDED, REMOVED, REASON,
  CUSTOMER, NOTE) columns go by heading; without one, in the MOVEMENT LOG's
  order (date, item, added, removed, reason, customer). A blank date takes the
  row above's; the sheet's own words work for reasons (sold, damaged,
  purchase...). Rows whose item cannot be told apart are left out and listed.
* **More than one warehouse**: pick it at the top left; every view, the count
  and the export follow it.
* **The entry row stays at the bottom of the sheet**, in sight however long the
  month is; a month opens at its newest lines. Saving keeps your place and the
  cell you moved to, and the line just saved lights up for a moment ("Saving…",
  then "Saved", at the top).
* **Undo** in the notice after: the quantity moving to the reason's column,
  Cancel line, Copy, Reverse and a paste (the pasted drafts go). A posted line
  is never undone this way.
* **Cancelled lines are hidden**; "3 cancelled" in the toolbar shows them.
* A draft shows only the quantity column its reason uses (the other appears on
  hover). The status says what a line needs in a word ("Short · 2 items");
  hover it, or select the line, for the whole reason.
* **Reasons and what posting creates**

  | Reason | Goes in | Posts as |
  |---|---|---|
  | Bottle filling | Added | Manufacturing order (filling) |
  | Production batch | Added | Manufacturing order (batch) |
  | Received | Added | Confirmed purchase order and its validated receipt (supplier and cost per unit required) |
  | Customer return | Added | Return from the customer |
  | Sale | Removed | Delivery to the customer |
  | Spoiled / Sample or gift / Used in-house | Removed | Scrap |
  | Correction | Either | Inventory adjustment |

* **Filling and production fill in their components from the recipe** (Bill of
  Materials) for the quantity made. Pieces (cartons, bottles, labels, stickers)
  round up to whole units; liquids and powders are exact. Change any quantity,
  remove a component that was not used or add one that was; *Back to recipe*
  undoes it. Products with several recipes (65 ml by fragrance) pick one in the
  side panel.
* **On hand after** each line is the running balance: drafts are projected in
  date order on top of what is on hand now, so a line is **Short** before it is
  posted, not after. Short lines are blocked; a supervisor can *Post anyway*.
* Each line says what it still needs: item, reason, quantity, the right column,
  a recipe, a customer.
* **Posting** (supervisors) creates the real Odoo document and locks the line;
  mistakes are fixed with a new line, so history stays intact. *Post N ready*
  first says what it will create. Lines are numbered by reason and year from
  Odoo's gapless sequences (Settings > Technical > Sequences, *Stock Book: ...*):
  `FIL/2026/0014`, `OUT/2026/0112`. Lines posted together, or by two people at
  once, never share a number.
* **Back-dated lines** land on their day: the moves, the delivery, receipt or
  scrap, the manufacturing order and any journal entries are dated the line's
  date. Lines dated ahead wait (*Dated ahead*) until the day.
* **Undo** (supervisors), in a posted line's panel, takes the line back in one
  step by Odoo's own documents, all of it or none. The line stays on the book
  marked *Undone*, beside the lines that took it back, and a draft copy on its
  day is added to correct and post (*Remove the copy* in the notice if it is
  not wanted):

  | Line | Undo |
  |---|---|
  | Sale, return, scrap, correction | The opposite line, today, posted at once (a sale comes back as a customer return) |
  | Filling, production batch | Unbuilds its manufacturing order: what it made leaves stock, its components go back. The copy keeps the components as used |
  | Received (quick line) | Returns the goods to the supplier and takes them off the purchase order, which is cancelled when nothing else on it was received |
  | From *Add purchase* | Undo purchase, below: the whole LPO, opened again to correct |
  | From *Add sale* | The whole invoice: payment cancelled, invoice credited in full by a credit note, goods back in stock, order cancelled, then opened again to correct at its prices |

  It is refused, saying why, when what it would take out of stock has been
  used, when a receipt is billed, when a payment is matched with the bank, or
  when the line was received or delivered in Purchases or Sales, where it is
  undone instead.
* **Copy** a line to log the same movement again; **Reverse** (storekeepers) a
  posted sale, return, scrap or correction to add the draft that undoes it,
  for a supervisor to post, noted with the reference it reverses.
* **Remove pair** (supervisors): once a mistake and the line reversing it are
  both posted and cancel out exactly, the two come off the book together, with
  Undo in the notice. They are archived, not deleted: their stock moves and
  journal entries stay, stock on hand does not change, and their moves still
  count as logged. *All movements* shows them under *Removed with their
  reversal*. Lines of an LPO or invoice window are undone there instead.
* Items tracked by lot or serial number are posted and counted in Inventory,
  where the numbers are entered; the book says so before posting.
* Items a recipe uses that nobody counts, such as tap water: tick *Never short in
  Stock Book* on the product. Consumables Odoo keeps no stock of are never
  short either.
* Components follow the recipe as the manufacturing order will: kits are opened
  up into what they contain, recipe lines for other variants are left out, and
  a recipe written in grams for an item stocked in kilograms uses kilograms.

## Purchases

Goods bought and in the store take one line and one *Post*: type the item, the
quantity, the supplier and the cost per unit before VAT. Posting makes the
purchase order, confirms it and validates its receipt, so the stock goes up, the
cost is recorded and the order shows in Purchases, ready to bill. Lines posted
together for one supplier on one day share one order. A quick line has no invoice
number, so it is not billed: use *Add purchase* for that.

**Add purchase** is the same thing for a whole LPO or invoice, in one window: the
supplier, the LPO or invoice number, the date, and the items with quantity, cost
and VAT, with the subtotal, VAT and total as Odoo will compute them. *Save and
receive* makes one purchase order with the number as its Vendor Reference, and
adds one posted line per item here. A number already used for that supplier is
refused, so a delivery cannot go in twice. Storekeepers *Save for posting*: the
lines wait, carrying the same number, and post together as one order.

The window also does the accounting, in the same step and the same transaction,
so either all of it happens or none of it: a posted vendor bill in Vendor Bills,
dated and numbered from the paper, and the payment from the journal chosen in
*Paid from* (or *Not paid yet*, which leaves the bill open). Purchases paid from
a petty cash float go through the Petty Cash book instead, which receives the stock
itself; entering them in both would count them twice. In Odoo 19 a receipt
alone makes no journal entry: stock reaches the accounts with its bill. Lines
received another way show in *N to bill* on the book until they are billed.

A line can be something not kept in stock, such as transport: it goes on the
order and the bill, to its product's expense account, and not into stock or the
item's cost. At least one line must be stock; a purchase with none is a bill to
enter with *Add bill*, below.

Inline lines read the cost as before VAT and take the VAT last used for the item
or supplier, never one included in the price. In the window the VAT on each line
says how its cost is read.

A *Received* line with no cost asks for it: its status opens the window with that
line in it, and saving replaces the line. A draft LPO reopens with *Edit purchase*.

A receipt validated in Purchases, or a return to the supplier, adds its own
posted line here. Enter a delivery in one place or the other, not both.

## Bills (not stock)

**Add bill** (shortcut `B`) is for what the business buys and has already paid
that is not kept in stock: printing paper, electricity, a repair, rent. It is
Odoo's own vendor bill, without a product or a purchase order, in one window
and one step, and nothing of it goes on the book or into stock.

* **The paper as it reads**: the supplier (or a new one, created as typed), the
  bill or receipt number, the bill date, and where it was paid from. A line for
  each thing the bill is for: what it was for, the account it is charged to,
  the quantity (empty is 1), the unit price and the VAT, with each line's VAT
  amount and amount, and the bill's total by VAT rate, as the bill will show
  them, to compare with the paper.
* **The account**: costs (expenses, cost of revenue) and what is paid now and
  kept (prepayments, fixed assets). Never stock, receivable, payable, bank,
  tax or income: stock is bought with *Add purchase*. A supplier billed before
  starts its lines on the account its bills use most, and the list shows those
  accounts first; picking an account takes its default VAT, as Odoo's bill does.
* **The VAT** is chosen on each line, as for *Add purchase*: the price is read
  before VAT unless the VAT chosen says *(incl.)*.
* **The supplier's KRA PIN** and **CU invoice no.** (from the supplier's eTIMS
  invoice, by its QR code) are optional. The PIN is saved on the supplier; the
  CU number on the bill, as *Supplier eTIMS Invoice No.* under its Bill
  Reference, where it can also be added later, once the supplier sends it. A CU
  number already on another bill is refused.
* **The scan** is optional: a PDF or photo (JPG, PNG, WebP; up to 5 files of 10
  MB), attached or dropped on the window. It shows beside the bill in
  Accounting and in its chatter. Nothing is read from it into the bill.
* **Saving** posts the bill in *Accounting > Vendors > Bills*, dated and
  numbered from the paper, and pays it in full from the journal chosen, in the
  same transaction: all of it or none. The bill is in the accounts on the day of
  the paper, unless a lock date closes that day: then on the first open one, as
  Odoo dates it. A number already used for that supplier is refused, as in *Add
  purchase*.
* **Who**: anyone who uses the book, storekeepers included. The bill and the
  payment are made for them without accounting rights, recorded as theirs. It
  is corrected in Accounting (*Reset to Draft*, or a credit note), not here.
* Journals are those whose payments reach the ledger, as for *Add purchase*:
  what a petty cash float pays for goes through the Petty Cash book.

### VAT on bills in Kenya

What KRA allows, so that a bill's VAT can be claimed and its cost deducted:

* Input VAT is claimable only on the supplier's **eTIMS (or TIMS) invoice that
  KRA has received, with the company's PIN on it**. A manual invoice or a
  receipt without it cannot be claimed. KRA pre-fills the purchases in the VAT
  return from what suppliers transmit, and may list a purchase by its
  CU invoice number rather than the number printed on the paper: that is what
  the CU number is for.
* It is claimable within **six months** (KRA's FAQ counts from the invoice date;
  its VAT brochure from the end of the month of the purchase). A supplier on
  KRA's VAT special table cannot be claimed from at all.
* For **income tax**, an expense needs an eTIMS invoice too (Finance Act 2023,
  Income Tax Act s.16(1)(c)), except wages under PAYE, imports, bank and
  insurance charges, airline tickets and other exempt cases. From January 2026
  iTax checks expenses against eTIMS when the return is filed.
* An invoice can be checked by scanning its QR code, or with the invoice number
  checker on iTax.

The window says so, without stopping the bill, when it has VAT and no CU number.
Sources: KRA's FAQs *The VAT auto-populated return* and *Learn about eTIMS*,
KRA's VAT brochure (reviewed October 2024), KPMG Kenya's tax alert *eTIMS and the
Shift to Data-Driven Income and Expense Validation* (2026).

## The cards

Each opens what it counts, says what those lines are, and offers the next step.

* **Needs something**: the lines missing a detail. A line's status is a button
  that goes to what is missing.
* **Short**: the lines using more than is on hand, and each item short by how
  much, with *Buy* or *Make*, and *Count*.
* **Ready to post**: the lines ready, with *Post*. Posting goes in date order, so a
  delivery lets the use after it through. A line that cannot go stays a draft and
  is listed with why; the others still post.
* **Out for recipes**: every item at zero that a live recipe needs, the recipes
  waiting, how fast it goes and where it last came from. Tick several and *Add
  purchase*, or *Make* what has its own recipe.
* **Stock value** opens the stock sheet.

## Stock sheet

Opening + added − removed = closing for the month on the tab, by category, with
cost, value and weeks of cover (last four weeks' use). Quantities are what was
really moved (a filling that used 232 bottles where the recipe said 230 shows
232); transfers to and from another warehouse count, moves between shelves of
the same warehouse do not. Months run on the user's time zone. Filters for finished
goods, raw materials, packaging, moved this month, out of stock.

## Count

Type what is on the shelf; the difference and its value show at once. Enter goes
to the next item. *Post count* (supervisors) applies all counted items as one
inventory adjustment. The count is for the whole warehouse stock, shelves
below it included.

When stock moved after an item was counted (*moved since*), *Post count* stops
and asks: *Movements came after the count* applies what was found missing or
extra to what is there now; *The count saw them* sets the item to the number
counted. Nothing is posted until one is chosen.

## Export and print

Excel with two sheets, MOVEMENT LOG (components under each filling or batch)
and MAIN INVENTORY, for the month on the tab.

*Print* gives the month's movement log as a PDF to sign and file (A4
landscape); the print button on a day's heading prints that day. Cancelled
lines are left out; lines not posted yet are marked, and the sheet says it is
not final while there are any. It ends with the lines by reason and a sign-off
for the storekeeper and the supervisor. The PDF needs wkhtmltopdf on the
server, as every Odoo PDF does.

## Light and dark

The page follows Odoo's dark theme. Status colours, the stripe on each row,
red negatives, green additions and the reason dots have light and dark values,
checked for contrast (text 4.5:1, stripes and dots 3:1) in both.

## Access

*Stock Book: Storekeeper* types lines and counts; *Supervisor* posts. Inventory
administrators are supervisors; a supervisor need not be one. Posting,
cancelling and references are the book's own: a line cannot be marked posted,
turned back into a draft or renumbered by hand, from the form or over the API.

## Upgrading to 19.0.1.11.0

Upgrade the module (Apps > Stock Book > Upgrade) for *Add bill*: it adds the
*Supplier eTIMS Invoice No.* to vendor bills (the same field as the Kenyan eTIMS
module's, when that is installed).

## Upgrading to 19.0.1.9.0

Upgrade the module (Apps > Stock Book > Upgrade) for Undo: it adds the
*Undone* marks to movements and sales and the credit note to sales. Purchases
undone before keep their *Undone* mark.

## Upgrading from 19.0.1.0.0

The upgrade starts each reason's numbering after the highest reference already
in the book, renumbered lines included, and warns in the log about any
reference two lines still share.

## Stock in the accounts

- **Add purchase** and **Add sale** make Odoo's own documents in one step: order, receipt or delivery, bill or invoice, and payment. The customer invoice posts the cost of sales.
- **Print**: with Business Documents installed, a posted line's panel has a button for each document of what it posted (delivery note, GRN, sales invoice, purchase order), and saving a sale or purchase offers them in its notice.
- **Add sale** prices each line as a sales order would: the customer's pricelist (*Sales > Customers*, Sales & Purchase tab), at the line's quantity, so minimum-quantity prices apply, and on the delivery date. A customer not created yet gets the first pricelist. The price can be typed over and then stays; until it is, a new quantity, customer or date prices it again. Hover the price to see which pricelist it came from.
- **Add sale** numbers the invoice with what is typed in *Invoice no.*, QuickBooks's number, not by Odoo's sequence; a number another invoice already has is refused. An undone sale gives its number back: its credited invoice is renamed *<number> (undone)*.
- **Add sale** also shows the customer's **KRA PIN** and **Payment terms**, filled in from the customer. Either can be typed in where it is missing, or corrected, and is saved on the customer for the next sale; an empty box takes nothing off the customer. A PIN typed must be a KRA PIN (A or P, nine digits and a letter); one the customer already has, a foreign tax ID say, stays as it is. **Due date** shows the day the invoice falls due, as Odoo dates it from the terms and the delivery date; with no terms it is due on that date. **CU invoice no.** is the number KRA's eTIMS gave the QuickBooks invoice: with Business Documents installed it prints on the invoice, under the invoice number, which is the one typed in *Invoice no.*. A CU number already used is refused, except one on a sale that was undone: that signed invoice is entered again, corrected, and the window Undo opens again keeps its PIN, terms and CU number.
- Scrap and corrections post to `500106 Stock Losses & Adjustments`, through the account on the Inventory adjustment location.
- On the first of each month, a scheduled task runs Odoo's Stock Closing for the month before, bringing the Stock account to the value of the stock. Odoo's own monthly schedule does not fire: it filters on two periods at once at month end.
- **Undo purchase** cancels the payment and bill, returns the goods and cancels the order, all or nothing. It is refused if the goods are used, or the payment is matched with a bank statement.
- **Undo** on a sale from *Add sale* credits the invoice with a posted credit note matched to it (Odoo's Reverse, full refund) rather than cancelling it, so the invoice numbering and any tax-authority submission stay whole.
- Every stock change made outside the book lands on the movement log, posted.

For past data, as an accounting administrator (each only reports until `dry_run=False`):

- `book_bill_imported`: bills orders whose receipts were cancelled, with no stock added.
- `book_ledger_setup`: the account above, the monthly closing, and invoices required on sales.
- `book_stock_closing`: Odoo's Stock Closing, previewed then posted.
- `book_log_missing`: logs past moves that are not on the book.
