# BizSure Quotation Catalogue

The rate cards, as Odoo data. Install it and the sales team has 16 templates
to quote from in **Add Medical Quote** (`odin_medical_quote`), and
`odin_client_quote` prints them as a client quotation or a side-by-side cover
comparison.

## What lands

| | |
| --- | --- |
| Product categories | 23, under `Insurance Premiums` |
| Taxes | Training Levy 0.2%, PHCF 0.25%, in an `Insurance Levies` group |
| Products | 278, each at its real annual premium |
| Quotation templates | 16 — APA Jamii Plus ×8, Birdview Bizsure Afya ×8 |
| Template lines | 691 |
| Insurers | APA Insurance, Birdview Microinsurance (contacts, set on their plans); Jubilee Health Insurance, with GA Insurance from the motor rates |
| Cover Quotes products | 43 more plans, with 138 rate tables and 451 rates: GA Community Health ×26, Jubilee J Care ×6, J Care Junior ×6, J Senior ×5 (see below) |
| Instalment plans | 4, 6 and 10 instalments on the NCBA IPF rate card of 4 March 2026 (band fees, flat rate from KES 300,000, none under 25,000) |

The 16 templates are the refined ones. The Motor, generic Health and
Family/Life templates that exist on the demo are **deliberately excluded** —
see *Known issue* below.

## Install

Depends on `odin_client_quote` (it defines the `cover_limit`,
`benefit_label` and `benefit_key` fields these templates set) and
`odin_medical_quote` (the insurer and medical-quote fields on the templates).
Upgrading this module installs `odin_medical_quote` if it is not there yet.

```bash
odoo -d <db> -i odin_client_quote,odin_medical_quote,odin_bizsure_quotes --stop-after-init
```

Then check two things Odoo cannot infer:

1. **The levies have no tax account.** `account.tax` builds its own
   distribution lines, but the account each levy posts to is left unset.
   Set it under *Accounting ▸ Configuration ▸ Taxes* before invoicing.
2. **The company document layout.** Set the name, address, logo and brand
   colours under *Settings ▸ Companies*; the reports fall back to a navy
   `#003478` but the letterhead is your data.

## Changing a rate

Rates are kept in **Cover Quotes ▸ Products** by the office (Sales
Administrators). Every plan and motor rate this module installs appears
there as a product with its rate tables; a change made there is what New
quote prices from the next quote on.

The products and plans are installed `noupdate`: an upgrade of this module
never puts the shipped rates back over the office's. `data/source/*.json`
and `tools/generate_data.py` remain the record of the card as first loaded,
and still build a fresh database identically, but regenerating them does
not change a database the module is already installed on.

## The insurers

`data/insurer.xml` creates **APA Insurance** and **Birdview Microinsurance**
as company contacts, and every template names its insurer, so Add Medical
Quote filters and groups plans by insurer. They are `noupdate`: add their
address and phone in Contacts, an upgrade will not overwrite them.

## Every outpatient limit on the APA family plans

The APA rate card sells outpatient at 50,000, 75,000, 100,000, 150,000 and
200,000 per person with any family plan, but the templates offered only
100,000. Each APA family template now lists all five, each with its adult and
child row, so the quote builder offers every tier priced for the family's
ages. They are lines 873 to 928 of `sale_order_template_line.json`; nothing
already in the file changed.

## Template line ids

Template lines are named after the rate card's own line id
(`sot_..._line_422`), not their position in the template. Named by position,
inserting a line renumbers every line after it, and an upgrade then tries to
turn a note into a product line, which Odoo refuses (*You cannot change the
type of a sale quote line*). The upgrade that brings in this naming creates
the lines afresh and removes the old ones, once; quotations never point at
template lines, so nothing else moves.

## How the comparison gets its numbers

Each `line_section` carries three fields the reports read:

* `benefit_label` — the short client-facing name (`Inpatient`, `Outpatient`)
* `benefit_key` — what lines this benefit up against the same benefit on
  another plan
* `cover_limit` — what the benefit pays out

They are derived from the section title by `tools/generate_data.py`. Two cases
the derivation handles deliberately, both of which bite a naive reading:

* `Inpatient – Plan 1: KES 200,000 per family, including maternity KES 40,000`
  takes the **first** KES figure. The second is a maternity sub-limit, not the
  plan limit.
* `Optional – dental cover (per person, with outpatient cover)` is a **dental**
  section that merely mentions outpatient. The derivation picks whichever
  benefit is named first, so APA dental and optical premiums do not get merged
  into the outpatient row.

Sections of add-ons are flagged `is_optional`, which is Odoo 19's own
section-level flag — a line is optional because its parent section is.

## GA Community Health, Jubilee J Care, J Care Junior and J Senior

Added as Cover Quotes products (Cover Quotes ▸ Products), each with its rate
tables, the way the office adds a plan: saving a product makes its quotation
template, with the statutory charges and payment options of the latest plan.
`tools/rate_cards.py` is the cards transcribed row for row, as printed;
`tools/generate_cover_products.py` writes `data/cover_product_ga.xml` and
`data/cover_product_jubilee.xml` from it. Both load once (noupdate): the rates
are the office's after that.

| Card | Plans | Priced |
| --- | --- | --- |
| GA *Community/Retail Health – Inpatient Only*, effective 1 Jan 2022 | 26: limits 250,000 to 10M, each excluding or including pandemic | By family size, M to M + 6; maternity 50,000 to 300,000 per family |
| Jubilee *J Care*, effective 1 Dec 2023 | 6: Classic to Prestige | Per person on the eldest's age band; options: outpatient (per person, and shared per family on Royal and Prestige), maternity, dental, optical, last expense, personal accident, enhanced Covid-19 |
| Jubilee *J Care Junior* (children 0–17 as main member) | 6: Classic to Prestige | Per child; outpatient, dental, optical |
| Jubilee *J Senior*, effective 1 Dec 2023 | 5: Copper to Diamond | Per person on the eldest's age band, children on their own rates; outpatient, dental, optical, last expense, personal accident, enhanced Covid-19 |

How the cards were read, where they leave room:

* **"Premium will be based on the age of the oldest applicant"** (Jubilee,
  General Conditions 6). The inpatient, outpatient and J Senior last expense
  tables are rated on the eldest's age (`age_basis = eldest`): a principal of
  35 with a spouse of 42 both pay the 41–50 rates, and J Care children pay
  the family's outpatient band, as there is no child row. A child row (J Care
  and J Senior inpatient, J Senior outpatient) is the child's own rate.
* **Free for the principal, optional for dependants.** Last expense is sold
  for dependants and personal accident for dependants of 18 and over; the
  others are left off (`partial`), and a family with nobody to sell it to is
  not offered it. Children are quoted by number, not age, so a dependent child
  of 18 to 25 is not offered personal accident.
* **Jubilee maternity** says "premium per principal/spouse" with the limit per
  family: one premium per policy.
* **Shared outpatient per family** (Royal and Prestige) is a second outpatient
  option; the card's "--" sizes are not quoted ("Rated for families of up to
  3 people").
* **Dental and optical** need outpatient, on both Jubilee cards.
* **Enhanced Covid-19** is offered on J Care and J Senior, not J Care Junior:
  the Junior table lists only inpatient, outpatient, dental and optical.
* **GA ages.** The family-size rates carry ages 19 to 60, the adults' last
  joining age: a family whose eldest is 61 or over is told so (GA sends
  seniors to its Hadhi Plan). A renewal of a member aged 61 to 65 is not
  re-quoted on these rates.
* **GA including pandemic** is its own plan at each limit, priced as the
  card prints it, not a loading on the excluding-pandemic rate.
* **J Senior day case admission**: the card prints "covered under applicable
  sub-limit" under Bronze, Silver and Gold only; the Copper and Diamond notes
  leave it out, as the card does.

## Motor rates

`data/motor_rate.xml` is Bizsure's motor rate card from
*MOTOR_RATES_REVAMPED_1.xlsx* (selling premiums): 87 lines for Definite
Assurance, Fidelity, CIC, GA, AMACO and Pioneer. It covers comprehensive
rates by value band with minimums and vehicle age limits, and third-party
premiums by class, tonnage, passengers and period, with PVT, excess
protector, PLL and courtesy car as each insurer offers them. It is loaded
once (noupdate), then maintained in Cover Quotes ▸ Configuration ▸ Motor
rates. `tools/generate_motor_rates.py` rebuilds it.

Read from the sheet:
* **AMACO's** "MIN 42,500" is a minimum premium.
* **Fidelity's** age column (15 to 18 years by band) is a maximum vehicle
  age.
* **Pioneer's** "Assets" line is left out (unclear).
* **GA's** 3 to 8 million band has no rate in the sheet, so GA does not
  quote that band.

`data/motor_product.xml` puts the training levy and PHCF on the motor
products, as on medical premiums.

## Tests

`tests/test_quote_example.py` prices the brief's worked example from these
rate cards -- principal 35, spouse 28, three children, Birdview plans 1 to 3
-- and checks 50,317, 69,919 and 84,857 to the shilling, with taxes and
levies of 265, 353 and 420. It also prices quotation #1323 as the client
received it -- Birdview Plan 4 for two adults of 42 and three children,
outpatient 100,000, dental and optical 10,000: 90,614 premium, 447.76 taxes
and levies, 91,061.76 total (Odoo rounds the two levies to the cent each, so
the total can be one cent above a single 0.45% figure) -- and checks the
export shows it that way. It also checks the insurers and the five APA
outpatient tiers.

`tests/test_new_cards.py` prices GA, J Care, J Care Junior and J Senior plans
for families read off the cards: J Care Classic for a principal of 35, a
spouse of 42 and three children is 73,514 inpatient (23,215 + 19,453 +
3 × 10,282, the 41–50 rates), outpatient 5 × 30,315; GA KES 1M for five is
72,915 excluding and 87,498 including pandemic; and the age limits, the
shared outpatient sizes, and the covers free for the principal.

## Known issue: the Motor templates are not shipped

The Motor rate products on the demo store a **percentage in the price field**:
`MTR-COMP-SAL` ("Motor Private Comprehensive – Saloon (4.0%)") has a list
price of **40**, and `MTR-ADD-PVT` ("Political Violence & Terrorism (0.25%)")
has **2.5**. A quotation built from those templates and not hand-overridden
line by line quotes a saloon comprehensive at KES 40.

Rating motor premiums off a sum insured needs either a computed line or a
pricelist keyed on vehicle value; it is not a catalogue problem. Until that is
built, those templates stay off production.
