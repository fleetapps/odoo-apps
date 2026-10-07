# BizSure Quotation Catalogue

The rate cards, as Odoo data. Install it and the sales team has 16 templates
to quote from; `odin_client_quote` then prints them as a client quotation or a
side-by-side cover comparison.

## What lands

| | |
| --- | --- |
| Product categories | 23, under `Insurance Premiums` |
| Taxes | Training Levy 0.2%, PHCF 0.25%, in an `Insurance Levies` group |
| Products | 278, each at its real annual premium |
| Quotation templates | 16 — APA Jamii Plus ×8, Birdview Bizsure Afya ×8 |
| Template lines | 635 |
| Instalment plans | 3, 6 and 10 months at 25% deposit + 3%/month |

The 16 templates are the refined ones. The Motor, generic Health and
Family/Life templates that exist on the demo are **deliberately excluded** —
see *Known issue* below.

## Install

Depends on `odin_client_quote`, which must be installed first (it defines the
`cover_limit`, `benefit_label` and `benefit_key` fields these templates set).

```bash
odoo -d <db> -i odin_client_quote,odin_bizsure_quotes --stop-after-init
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

## Known issue: the Motor templates are not shipped

The Motor rate products on the demo store a **percentage in the price field**:
`MTR-COMP-SAL` ("Motor Private Comprehensive – Saloon (4.0%)") has a list
price of **40**, and `MTR-ADD-PVT` ("Political Violence & Terrorism (0.25%)")
has **2.5**. A quotation built from those templates and not hand-overridden
line by line quotes a saloon comprehensive at KES 40.

Rating motor premiums off a sum insured needs either a computed line or a
pricelist keyed on vehicle value; it is not a catalogue problem. Until that is
built, those templates stay off production.
