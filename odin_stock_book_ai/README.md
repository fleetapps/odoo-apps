# Stock Book: invoice photos (`odin_stock_book_ai`)

**Upload photo** in the Stock Book's **Add sale** window. The storekeeper takes
a phone photo of each page of the printed, signed customer invoice. The window
is filled in from it: the customer and its branch, the invoice no., the date,
the payment terms and due date, the CU invoice no., and every line with its
item, quantity, rate and VAT. The person checks it against the paper and saves
it, or saves it for a supervisor, as any other sale. **Reading a photo saves
nothing, posts nothing and changes no customer.**

Odoo 19. A bridge between Stock Book (`odin_stock_book`) and Accounting AI
(`odin_account_ai`): it installs itself when both are installed. Stock Book
does not need the AI. The AI's consent, API key, model, monthly budget and
Activity Log are Accounting AI's (*Accounting > Configuration > Settings >
Accounting AI*); the calls show there as *Stock Book photo*.

## In the window

1. *Upload photo* (or drop the photos on the window): one photo per page,
   JPG, PNG, WebP or PDF, up to 6 of 10 MB. On a phone the button opens the
   camera or the gallery.
2. *Reading the invoice…*: one request, which waits at most 75 seconds (Odoo
   stops a request after two minutes). When lines are left between
   close items, a second step chooses among them (*Choosing between similar
   items…*). *Stop* leaves the window as it was.
3. The window is filled in. Above it, what concerns the whole invoice: a page
   missing ("Page 1 of 2" with one photo), lines that do not add up to the
   total printed, a date in the future, terms that do not give the due date
   printed, a CU number already used, a branch that is not in Odoo.
4. Under each line, what the invoice prints (`HAIR SHAMPOO AVOCADO &
   GRAPESEED 500 ML · 6 × 285.00 = 1,710.00 · 16.0% S`), to compare with the
   item chosen. A line to check is highlighted, with why, the other items it
   may be (one click to switch), and **Looks right**. The sale cannot be
   saved until each highlighted line is looked at: *Looks right*, another
   item picked, or a number or the VAT changed. Hover the printed text to see
   the near-misses ruled out ("Not Avocado & Grapeseed Conditioner 500 Ml: a
   conditioner; the invoice says shampoo").
5. Under the totals, the window's total against the invoice's.
6. *Add a page* reads all the pages again together; *Read again* reads them
   afresh. A page can be taken off with its ×.

Saving works as before: *Save and deliver* (supervisors) or *Save for
posting*. The photos go with the sale, and a copy is posted in its customer
invoice's chatter, beside it in Accounting. A draft keeps them: opened again,
the window shows them, and when the sale is posted the photos reach its
invoice. *Undo* on a sale opens it again with its photos, and the sale
entered again takes them (the credited invoice keeps its copy). Photos read
in a window closed without saving are deleted after 30 days.

## What is checked, line by line

The model **reads**; the item is chosen **here, in code**, from the catalogue
(`models/matching.py`): every product that can be sold, stock or not
(delivery, say). In this order:

* **The code**, when the invoice prints it. A code that says conditioner on a
  line that says shampoo is taken but highlighted.
* **The kind of product**: shampoo, conditioner, lotion, handwash, dishwash,
  multipurpose, bleach and so on, with the ways invoices write them (COND,
  H/WASH). **An item of another kind is never matched**: the shampoo of a
  range is never its conditioner (FIN-PAK-040 and FIN-PAK-117, FIN-PAK-044
  and FIN-PAK-119), the baby shampoo never the baby lotion.
* **The size**: 500 ml, 1000 ml and 1 l, 5 litre, 250 g. **Another size is
  never matched.** No size on the invoice, and the item comes in several:
  highlighted, between those sizes.
* **Bulk or packaged**: **an item kept in bulk (a *Bulk* category, or *Bulk*
  in its name or code) is matched only when the invoice says bulk**.
  "MULTIPURPOSE REG 1000 ml" is the 1000 ml bottle, never Multipurpose
  Regular (Bulk) in litres.
* **The other words**, weighed by how rare they are in the catalogue
  ("grapeseed" says more than "hair"), with abbreviations (REG, AVO) and
  words run together (grape seed, grapeseed).
* To break a tie: the customer's **pricelist price** at the rate printed, and
  what the customer **bought before**.

A line is *sure* when one item stands out with nothing against it; then
nothing is asked. Otherwise, when the rules leave a few close items, the
model chooses among **those only** (a letter it was not offered is ignored),
and a choice it is not sure of stays highlighted. With no item close enough,
the line stays empty with the printed text in the item box, to search from.

Then each line is checked:

* **The customer's pricelist**: the rate printed against the price a sales
  order would give the customer for the item, at that quantity and on that day
  (*Sales > Customers*, Sales & Purchase tab), both read the way the line's
  VAT reads a price. A difference is highlighted ("The invoice's rate is
  285.00; the Chandarana price is 300.00"). The invoice's rate is what goes
  in: it is what the customer was charged.
* **The VAT**: the rate printed ("16.0% S", "0% Z") picks the sales VAT of
  that rate, the item's own when it has that rate. Rates are read as
  including VAT, as the store's invoices print them, unless the paper's
  arithmetic says otherwise; with a VAT that reads prices before VAT, the rate
  is converted and the line says so.
* **Quantity × rate against the amount printed**: a difference means a number
  is misread.
* **Handwriting** on a line other than a tick (a quantity crossed out and
  another written) and anything the model could not read for sure are
  highlighted. The printed quantity is put in; the person decides.

## The customer

Found by its KRA PIN or its name, and its **branch as a contact under it**
(*Chandarana Supermarket, Hookwood Square - Brookside Grove*), whether the
branch is printed on its own line or run into the name. When it is not sure,
the box holds the printed name and the window offers the customers it may be.
A branch printed that is not a contact under the customer is said; the
customer is not picked for the person then. Nothing is created: a customer
not in Odoo is created by the person, as before.

The PIN the customer has on file stays (a different one on the invoice is
said). A PIN or terms the customer lacks are saved on it when the sale is
saved, as typed ones are.

The window's own customer search (`book_sale_customers`) still lists
companies only; finding branch contacts there is a separate change to Stock
Book.

## The data sent to Anthropic

Only once *Send accounting data to Anthropic* is ticked for the company
(Accounting AI). Each upload makes one or two calls to the Claude API, through
Accounting AI (official `anthropic` SDK, its model setting, `claude-opus-5-5`
by default):

1. **Reading** (effort low): the pages, each a copy made for the model
   (turned upright, JPEG, at most 2576 pixels on the long edge, the most the
   model reads; a PDF as it is), the company's name and KRA PIN (so that the
   customer is the other party), today's date, and the fixed instructions.
   The answer is JSON of a fixed shape (structured outputs): what is printed,
   nothing chosen.
2. **Choosing**, only when lines are left between close items (effort
   medium): for each such line, what was read on it (description, code, unit,
   quantity, rate, VAT), and up to six catalogue items: code, name, category,
   unit, this customer's price, whether the customer bought it before. No
   photo, and not the customer's name.

Never sent: the customer list, the rest of the catalogue, stock, other sales.
The instructions tell the model that the text in the photos is data, never
instructions. The answers are checked here: an item or letter it was not
offered is ignored, and nothing it says is saved without the person.

Each call is logged in Accounting AI's *Activity Log* with its tokens and
counts against the company's monthly budget. A request Odoo retries reuses
the stored answer rather than paying twice. Stored answers are erased after
Accounting AI's retention period. What was read stays with the photo
(`odin.stock.photo`) for the audit: the photos, what was read and what the
window was filled in with.

## Install

1. Accounting AI set up: `anthropic` installed on the server, the consent
   ticked, the key in place (its README).
2. Upgrade Accounting AI (19.0.1.1.0: a call can set its effort and output
   length) and install *Stock Book: invoice photos*, if it has not installed
   itself.

The button says why it is greyed out when the consent, key or budget stops
it.

## Tests

The API is never called: the tests replace the one network call with answers
shaped like the API's. They cover the matching rules (shampoo and
conditioner, lotion, sizes, bulk and packaged, codes, the customer and
its branch), the pricelist and VAT checks, misread numbers, pages and totals,
the request sent, and the photos going with a sale, a draft, its invoice and
an undo.

```
odoo-bin -d test -i odin_stock_book_ai --test-tags /odin_stock_book_ai --stop-after-init
```
