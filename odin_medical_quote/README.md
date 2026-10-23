# Medical Quotes

**Add Medical Quote**: quote family medical cover on one screen while the
client is on the phone, keep the client and the opportunity in CRM, and turn
the plan they accept into an order and an invoice.

## Why

The Cover Quotes app could already price and print a comparison, but getting
there took a form, a household wizard that wants ages typed as a sentence, a
second wizard for optional benefits, and the quotations themselves opened one
by one. None of it touched CRM: on Bizsure not one of the quotations was
linked to an opportunity, so the pipeline never saw a quote, and a quote won
had to be confirmed and invoiced by hand.

The broker's own quoting tool works the way an agent thinks: who is covered,
what the client can afford, every plan in that range with every option
priced for this family, the chosen plans side by side. This is that screen,
on the rate cards already in Odoo.

## The screen

**Cover Quotes ▸ New medical quote** (the app opens on it), **Medical Quote**
on any CRM lead or opportunity, or **New medical quote** above the list of
quotes. Four steps on one page:

1. **Client and cover details.** *Customer source*: a new prospect (name,
   phone, email, lead source), an existing customer, or a lead or
   opportunity in CRM, each found by name, phone or email. A new prospect
   whose number or email is already in Contacts is pointed out, so the agent
   uses the contact instead of making a second one; an existing customer who
   already has an open opportunity is pointed to it, so the quote joins that
   opportunity rather than opening a second one. Then the principal's date
   of birth, spouse, number of children (or children only), the inpatient
   limit range, an insurer and a plan.
2. **Cover options and premiums.** Every plan in range that covers this
   family, one row each: inpatient, then each optional benefit -- outpatient,
   maternity, dental, optical, riders -- with every tier priced for these
   people and a **Select** button. Picking an option puts its plan on the
   quote. Plans the family cannot take are listed underneath with the reason
   (*For families where everyone is under 70*, *No rate for the principal at
   age 76*, *Rated for families of up to 6 people*).
3. **Compare plans.** The chosen plans as cards: inpatient and each option
   with *Remove*, a **discount** (`10%` or `2,500`), **taxes and levies**
   (levies plus stamp duty), a **policy charge**, the **total premium** and
   what paying monthly looks like. One plan can be marked recommended.
4. **Save.** See below. The page then offers **Print quotation**, **Send by
   email**, Share on WhatsApp, Open quote, Open opportunity, **Client
   accepted** per plan, and a link to record that the client declined.

Every figure on the page comes from the server, priced the same way the
quotation is built, so what the agent reads out is what the quotation says.
The worked example in the brief -- principal 35, spouse 28, three children,
Birdview plans 1 to 3 -- comes out at 50,317, 69,919 and 84,857, as on the
broker's screen; `odin_bizsure_quotes/tests` checks it.

Dental and optical that an insurer only sells *with outpatient cover* (the
section title says so) cannot be picked without it, and are taken off with
it.

## Saving: Contacts, CRM, quotations, comparison

* **Contacts.** A new prospect becomes a contact, the phone number stored in
  international form (`0798 726 720` → `+254798726720`) like the rest.
* **CRM.** A new opportunity, *Medical cover – Henry (5 people)*, in the
  agent's pipeline with the lead source. A lead picked from CRM is converted
  to an opportunity for its contact, the way *Convert to opportunity* would.
  Expected revenue is the recommended plan's premium, or the cheapest. A new
  quote sets the opportunity's expected closing to the day the quote lapses
  (unless one is set) and, unless the opportunity already has a next step,
  schedules a *Follow up on medical quote QC00012* to-do for its salesperson
  two days out. A lead nobody was assigned to goes to the agent who quoted it.
* **Quotations.** One per plan, against the opportunity (sale_crm's
  *Quotations* button counts them), carrying the opportunity's team,
  campaign, medium, source and tags. Only what was chosen is on it: the
  sections, priced lines and benefit notes of the cover picked, the discount,
  the template's charges, the policy charge and the payment terms. Sections
  are not left *optional*: the lines are priced for this family and are not
  for the client to change on the portal.
* **The quote** is a cover comparison (`odin.quote.comparison`) of those
  quotations, with who is covered and a details strip, so Print and Share on
  WhatsApp work as they always have. It keeps what was entered: **Edit quote**
  reopens the screen as it was left, and saving again rebuilds the draft
  quotations in place and cancels those of plans taken off. A quotation
  someone added to the quote by hand is left alone. Options are remembered
  by benefit and rate (`outpatient/limit 50,000:…`), not by template line, so
  a saved quote reopens on the same options after its template is edited.

## The quotation the client gets

**Print quotation**, **Send by email** and **Share on WhatsApp** all send the
same PDF, *Medical quotation - David Muthuri Muriuki - QC00012.pdf*, laid
out like the broker's own quotations:

* the letterhead -- the company's logo, name, address, phone and email;
* *Prepared for* the client, beside the quote number, date, validity and
  subject;
* **Member details**: the dates of birth, children and limit range exactly
  as entered (dates written `21 Jan 1984`, never `01/21/1984`);
* **Cover and premium comparison**: one column per plan (only the accepted
  one once a plan is accepted), insurer and plan at the top, the recommended
  plan marked; the limit of each benefit, then each premium, the discount,
  *Taxes and levies* (levies and stamp duty together, as the client pays
  them), the policy charge, the **total premium** and each instalment plan's
  deposit and monthly amount;
* the plans' **terms**, once each even when several plans share them;
* the validity, *Quotation by* the salesperson and their phone, and the
  **payment options** box;
* *By: <user> on <date>* and *For and on behalf of <company>* on every page.

The payment options are set once in **Sales ▸ Configuration ▸ Settings ▸
Quotations ▸ Payment details on medical quotations** (bank account, branch,
SWIFT, M-Pesa paybill...). Left empty, the company's bank accounts are listed
instead.

**Send by email** opens the email composer on the template *Medical quote:
send to the client* (Settings ▸ Technical ▸ Email Templates, yours to
reword: an upgrade does not overwrite it) with the PDF attached, and marks a
draft quote sent. A quote built the old way, in the comparison form, still
prints the old comparison.

## Client accepted

On the saved screen or on the quote: pick the plan, then *invoice the full
premium*, *invoice a deposit* (the instalment plans' deposit %, 25% on
Bizsure) or *confirm only*. It confirms that quotation, cancels the others,
marks the opportunity won and opens the draft invoice.

A salesperson needs no accounting rights for this: Odoo creates an invoice
from a sales order on their behalf, as Create Invoice on the order does, and
leaves it in draft for accounts. *Post the invoice now* is offered only to
users who may post invoices; if posting is refused (eTIMS, a lock date) the
draft stays and says why.

If the order cannot be confirmed -- a sale exception rule, a credit limit --
its own dialog is shown and nothing else happens until it goes through.

A quotation confirmed some other way -- *Confirm* on the quotation, or the
client signing and paying on the portal -- accepts the quote all the same:
the other plans are cancelled and the opportunity won. Client accepted then
only invoices it.

## Client declined

**Client declined** on the quote (or *record that they declined* on the
saved screen) opens CRM's own *Mark Lost* dialog with its lost reasons.
Marking the opportunity lost -- there or anywhere in CRM -- cancels the
quote's open quotations and marks it *Declined*, so nothing is left in
Quotations for a client who said no. If the client comes back, **Edit quote**
and save: the quote is a draft again, the opportunity is restored and the
plans get fresh quotations.

## Quotes register

**Cover Quotes ▸ Quotes** lists every quote:
* the phone, who's covered, the plans, the premium, valid until (red once
  expired), handled by and the next activity;
* search by customer name, email or phone as typed (`07...` finds
  `+2547...`), plan or insurer;
* filters for open, accepted, waiting to be placed, declined, expired,
  expiring in 7 days, follow-up due, quote date, my quotes and handled by
  me;
* a pivot by salesperson and status.

Select quotes to print them together, or to change *Handled by* in place.

## Hand-over

**Client accepted** asks who places the cover with the insurer. The default
is **Settings ▸ Sales ▸ Hand accepted medical quotes to**, else the
salesperson. That person gets a **Place cover** to-do with a checklist,
editable in CRM ▸ Configuration ▸ Activity Types, however the quote was
accepted. Changing *Handled by* moves the to-do. *Waiting to be placed*
lists accepted quotes whose to-do is still open.

## How a premium is priced

From the quotation templates and their products, read the way the rate
cards are written. Each product has a **rate group** (the rows of one table
on the rate card) and says how it is priced, both read from its name:

| Name says | Priced | Example |
| --- | --- | --- |
| `– Member Only`, `– Member + 4` | by family size: 5 people is Member + 4 | Birdview inpatient, outpatient |
| `– Principal (21–40 Years)`, `– Spouse (…)`, `– Child (Day 1 to 20 Years)` | per person, by role and age band | APA inpatient |
| `per Person (41–54 Years)` | per person, by age band | APA outpatient |
| `– Main Member` / `– Each Dependant` | per person: principal, and everyone else | Hosi Cash |
| `per Person` | per person | dental, optical |
| anything else | once per policy | maternity, Femina, stamp duty |
| `Below 70` / `Above 70` | for families whose eldest is under / at least 70 | Birdview variants |

A template names one product of each group -- the 21-40 band, Member Only --
and the right row is found for each person. Where the rate card has no row
(a 76-year-old on a card that stops at 70, a family of 8 on one that stops at
6) the plan or option says so; nothing is quoted from the nearest row.

The fields are on the product, under **Sales ▸ Medical quotes**, editable for
a product whose name does not follow the pattern.

On the **quotation template** (Sales ▸ Configuration ▸ Quotation templates):

* **Offer in medical quotes** -- ticked on its own for a template with an
  `inpatient` section;
* **Insurer** -- grouping and the insurer filter; left empty, the first part
  of the template name is used;
* **Covers** -- family or children only, read from the core cover;
* **Inpatient limit** -- what the limit range matches, read from the cover
  limit of the inpatient section (or of its product).

The sections are read as `odin_client_quote` defines them: a section flagged
optional is a benefit offered option by option (each subsection, such as a
rider, a benefit of its own), any other section is core cover, and
`statutory_charges` / `payment_options` are carried as they are.

### Discount and policy charge

The discount is Odoo's own global discount lines (the ones Sales ▸ Discount
makes), on the premium only, so the levies are worked out after it. An amount
comes off the premium, not off the total with tax as Odoo's fixed discount
would. The company's discount product is used when it has one, otherwise
*Discount* from this module. The policy charge is a line of *Policy charge*
(`FEE-POLICY`, no tax -- set one on the product if your charge attracts it),
after the statutory charges.

## Install

Depends on `odin_client_quote` and `sale_crm`. For the Bizsure rate cards,
`odin_bizsure_quotes` sets the insurers and depends on this module, so
upgrading it installs this one.

On an Odin instance, run as root (each is a separate sparse clone; order
matters, this module first):

```bash
for m in odin_medical_quote odin_client_quote odin_bizsure_quotes; do
  curl -fsSL https://raw.githubusercontent.com/fleetapps/odoo-apps/19.0/tools/odin-install-module.sh | bash -s "$m"
done
```

The first restarts Odoo with the new module on the path; the other two are
installed modules, so they are upgraded, and the `odin_bizsure_quotes`
upgrade installs `odin_medical_quote` with it.

## Tests

`tests/` builds a small rate card in the shape of the real ones and covers
the name parser, pricing by family size, role and band, refusals, the limit
range and insurer filter, totals with discounts, saving to Contacts and CRM,
reopening and saving again (hand-added quotations, edited templates),
the follow-up activity, Client accepted as a salesperson without accounting
rights (full, deposit, confirm only, a blocked confirmation, a quotation
confirmed directly), Client declined and quoting again, the quotation PDF,
email and WhatsApp attachment, and a browser tour of the screen.
`odin_bizsure_quotes/tests` checks the brief's example and quotation #1323
(Birdview Plan 4: 90,614 premium, 91,061.76 total) on the real rate cards.

```bash
odoo -d <db> -i odin_medical_quote --test-enable --test-tags /odin_medical_quote --stop-after-init
```

## Tested against

Odoo 19.0, on a copy of the Bizsure setup (Kenyan chart, the levies, the 16
templates) upgraded the way the instance will be.
