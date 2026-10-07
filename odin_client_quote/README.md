# Client Quotations & Cover Comparison

Two PDFs written for the person paying the premium, and the small amount of
data they need.

## Why

A quotation in Odoo is a bill: *Description, Quantity, Unit Price, Taxes,
Amount*. That is the right shape for selling 40 chairs. It is the wrong shape
for selling cover, for three reasons that compound.

1. **A configurator's working notes end up in front of the client.** Quoting
   medical cover means pricing every family size and every optional rider, so
   the lines are all there and all but a handful sit at zero quantity. On one
   real quotation in the demo database, 42 of the 46 product lines are quoted
   at zero — and the stock report prints every one of them, with a quantity
   column of zeroes beside it.
2. **The description column carries internal names.** `[JP-IP3M-P21] Jamii Plus
   Inpatient (Family) – KES 3,000,000 – Principal (21–40 Years)` is a product
   code and a rate band. The client reads a SKU.
3. **Benefits are prose in a price cell.** A 250-word bullet list of what
   inpatient cover pays for is a note line, so it renders as a paragraph
   stretched across a table built for numbers.

And the quotation can only ever describe one plan. A broker offering four
limits sends four PDFs, and the client does the comparison by hand.

## What this module does

### Client quotation (`sale.order`)

An extra entry in Print, beside Odoo's own. It prints:

* **Your cover** — one row per benefit, with what it pays out and what it
  costs. Only lines actually being quoted.
* **Or pay monthly** — the deposit and monthly figure for each instalment
  option on the order.
* **Optional cover you can add** — every zero-quantity line, as a price list
  under its own heading, clearly outside the premium.
* **What each benefit covers** — the notes, set as lists.
* **Terms** — the quotation template's terms.

Product codes are stripped from descriptions (`[CODE] Name` → `Name`).

### Cover comparison (`odin.quote.comparison`)

It is its own app — **Cover Quotes** — so the sales team finds it without
being told where to look. The same menu is kept under Sales ▸ Orders, because
that is where salespeople already are.

Several quotations for one customer, side by side: one column per plan, one row
per benefit. Limits first (what each plan covers you for), then premiums (what
each plan costs), then taxes, the total, and any monthly options. One column can
be marked **Recommended**.

Every figure is read from its quotation at print time. Change a quotation and
reprint — nothing in the comparison is retyped, so the two can never disagree.

**You do not prepare the quotations first.** Open a comparison, choose the
customer, tick the plans under *Plans to compare*, and press **Create
quotations**: one quotation per plan is created for that customer and compared
here. Adjust cover — family size, optional benefits — with **Open quotations**,
then reprint. The plans are the input; the quotations are a by-product.

You can still go the other way, if the quotations already exist: select them in
the Quotations list and use **Actions ▸ Build cover comparison**.

### Getting it to the customer

**Print** is on the form in any state. A broker talks a customer through a draft
far more often than they send a final one, so it is not gated on *Sent*.

**Share on WhatsApp** renders the PDF, stores it with an access token, and opens
WhatsApp with a message and a download link for the customer's number. The link
needs no login but cannot be guessed — the same mechanism Odoo uses to share
documents outside the backend. Sharing marks the comparison as *Sent*.

A Kenyan mobile stored as `0715152515` is converted to international form from
the contact's country dialling code; sending the local form opens a chat with
nobody.

## The data it needs

Three fields on the **section** lines of a quotation, all optional:

| Field | What it is |
| --- | --- |
| `benefit_label` | The short name the client sees: `Outpatient`, not `Optional – outpatient cover: KES 50,000 per family` |
| `benefit_key` | What lines this benefit up against the same benefit on another plan. Defaults to the label, so plans that already name a benefit the same way line up on their own |
| `cover_limit` | What the benefit pays out, as opposed to what it costs |

So one section line carries both halves of the comparison: the limit row and
the premium row come from the same record.

Switch the columns on with the ⚙ icon at the right of the order lines list.

### Instalment plans

**Sales ▸ Configuration ▸ Instalment plans.** A plan is four numbers — months,
deposit %, interest % per month on the balance after the deposit, and a flat
fee per month — which between them cover the schemes in use:

* *Deposit of 25%, then 3 % per month on the balance, over 3 months* →
  `months 3, deposit 25, rate 3, fee 0`
* *Spread over 4 months with a KES 1,000 monthly charge* →
  `months 4, deposit 0, rate 0, fee 1000`

Tick **Instalment financing charge** on any product that carries the cost of
paying monthly rather than cover itself (e.g. `[IPF-3M]`). Client reports leave
those lines out of the premium they quote, so an instalment schedule is never
worked out on a total that already includes interest.

## Setting up the look

The reports take their colour from the company's document settings, falling
back to a navy `#003478`. Under **Settings ▸ General Settings ▸ Companies ▸
Document Layout**, set at minimum:

* the company **name and full address** — a letterhead reading `demo` with no
  address below it is most of why a quotation looks unfinished;
* the **logo**;
* **Colors** — primary drives headings, table headers and the document panel;
* **Layout** — any of them; these reports draw their own frame, but the rest of
  Odoo's PDFs use it.

## Product codes

Odoo builds a line description from the product's display name, which carries
`[BZ70-IP-P1-M+1]`. That is right in a warehouse and wrong on a quotation a
customer reads — and it reaches them through the portal too, where no report
formatting can strip it. `sale.order.line` therefore composes its description
with `display_default_code=False`, the framework's own switch, so the code is
never written in. Lines created before this keep their old description; both
reports strip the prefix when printing, so old and new agree on paper.

The code is still on the product, still searchable, still shown in the back
office product picker.

## The letterhead has a height budget

The letterhead is a running page header: wkhtmltopdf draws it inside the top
margin, so if it grows taller than `margin_top` minus `header_spacing` it prints
*over* the body. That is not a soft failure — it lands on the customer's name.

Measured at 188mm wide (A4 less the 11mm side margins) the current letterhead is
**16.4mm** against **25mm** available (`margin_top` 30 − `header_spacing` 5).
Keep the address to three lines, or raise `margin_top` in
`report/paperformat.xml` to match.

## Notes

* Both reports are additional Print entries. Odoo's own quotation PDF is left
  alone, so you can compare them and roll back by doing nothing. To send the
  client quotation by email instead, point the quotation mail template's report
  at `odin_client_quote.report_saleorder_client`.
* `static/description/preview_comparison.html` and `preview_quotation.html`
  render the two layouts in a browser, off a copy of the report stylesheet,
  for design review without a server.
* wkhtmltopdf runs an old WebKit: the stylesheet uses tables and points, no CSS
  variables, grid, or modern flexbox.

## Tested against

Odoo 19.0. Views validate against `odoo/addons/base/rng/*.rng`; data files
validate against `odoo/import_xml.rng`.
