# Stock Book

The store's MOVEMENT LOG and MAIN INVENTORY sheets, inside Odoo 19, on one page
with a tab per month. Named *Stock Book* so it sits beside Odoo's Inventory app.

## Movements

* **Type in the last row**: date (`3`, `3/11`, `3 Nov`), item (any words of the
  code or name: `lush 500`), added or removed, reason, customer or supplier.
  Enter moves along; the item remembers the reason it is usually logged with.
* **Reasons and what posting creates**

  | Reason | Goes in | Posts as |
  |---|---|---|
  | Bottle filling | Added | Manufacturing order (filling) |
  | Production batch | Added | Manufacturing order (batch) |
  | Received | Added | Receipt from the supplier (optional cost per unit) |
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
  mistakes are fixed with a *Correction* line, so history stays intact. Lines
  are numbered by reason: `FIL/2026/0014`, `OUT/2026/0112`...
* Items a recipe uses that nobody counts, such as tap water: tick *Never short in
  Stock Book* on the product.

## Stock sheet

Opening + added − removed = closing for the month on the tab, by category, with
cost, value and weeks of cover (last four weeks' use). Filters for finished
goods, raw materials, packaging, moved this month, out of stock.

## Count

Type what is on the shelf; the difference and its value show at once. Enter goes
to the next item. *Post count* (supervisors) applies all counted items as one
inventory adjustment.

## Export

Excel with two sheets, MOVEMENT LOG (components under each filling or batch)
and MAIN INVENTORY, for the month on the tab.

## Access

*Stock Book: Storekeeper* types lines and counts; *Supervisor* posts. Inventory
administrators are supervisors.
