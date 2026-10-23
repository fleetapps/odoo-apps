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
| Insurers | APA Insurance, Birdview Microinsurance (contacts, set on their plans) |
| Instalment plans | 3, 6 and 10 months at 25% deposit + 3%/month |

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

The XML in `data/` is **generated**. Editing it by hand is lost on the next
regeneration; editing a price in Odoo is lost on the next module update,
because these records are module-managed and not marked `noupdate`.

The rate card is `data/source/*.json`. To change a premium:

```bash
# edit data/source/product_template.json
python3 tools/generate_data.py
# then upgrade the module
```

That keeps the rate card in git, with a diff per rate change, and lets the
same module rebuild a second environment identically.

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

## Known issue: the Motor templates are not shipped

The Motor rate products on the demo store a **percentage in the price field**:
`MTR-COMP-SAL` ("Motor Private Comprehensive – Saloon (4.0%)") has a list
price of **40**, and `MTR-ADD-PVT` ("Political Violence & Terrorism (0.25%)")
has **2.5**. A quotation built from those templates and not hand-overridden
line by line quotes a saloon comprehensive at KES 40.

Rating motor premiums off a sum insured needs either a computed line or a
pricelist keyed on vehicle value; it is not a catalogue problem. Until that is
built, those templates stay off production.
