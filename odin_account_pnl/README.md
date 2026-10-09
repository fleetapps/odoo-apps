# Interactive Profit & Loss (`odin_account_pnl`)

The Profit & Loss of Odoo 19 Enterprise, rebuilt for Community, on one page.
Odoo 19 Community has the `account.report` models but no engine to run them
and no P&L (both are Enterprise), so this module brings its own engine and a
default layout.

Odoo 19 Community. Depends on `account` and `mail`; no external Python.

## What the page does

*Accounting → Reporting → Statement Reports → Profit & Loss*

The top bar follows Enterprise's order:

| Chip | What it does |
|---|---|
| 📅 Period | Last month by default, as Enterprise. This/last month, quarter, fiscal year (the company's fiscal year end), fiscal year to date or any dates. ◀ ▶ step to the previous or next period. *Divide into* months or quarters shows one column per period plus a total. |
| % Comparison | No comparison, previous period(s), same period last year(s), or custom, up to 12 periods. A growth % column turns green when the change is good for that line (more revenue, fewer costs). |
| Journals, Analytic, Partners | Narrow the journal items. The analytic filter **prorates**: a bill split 60/40 between two departments counts 60% in the first. |
| Entries | Posted only or with drafts, hide lines at 0, account codes, account groups, unfold all. |
| Budget | Adds Budget and % achieved columns; amounts are typed into the report. |
| Scale | Units, thousands or millions. |

Unfolding happens in place: a line opens on its accounts, an account on its
journal items, and every journal item opens its document (invoice, bill,
payment or entry) in the side panel.

**What's in this number.** Any line or account can be split by partner,
product, product category, month, journal or analytic account (one plan at a
time). The ten largest contributors show with their share, the rest under
*Others*. With a comparison, the split shows who moved the number.

**Ledger.** An account can show its journal items oldest first with a running
balance, as the General Ledger does.

**Audit a cell.** Clicking a figure lists the journal items behind exactly that
figure (that row, that column), largest first.

**Side panel.** A journal item opens beside the report, not instead of it:
its document, lines with their analytic split, the attached PDF or image,
the last notes, and ◀ ▶ (or J/K) to step through the other items behind
the same figure. *Open* goes to the full form; the breadcrumb comes back to
the report exactly as it was (options, unfolded rows, panel, scroll).

**Notes.** *⋯ → Annotate* on a line or account. Notes show as a flag on the
row and as numbered footnotes in the PDF.

**Budget.** Pick or name a budget with the Budget chip: a Budget and a % column
appear, and an accountant types an account's budget straight into its cell. A
figure typed in a quarter or year column is spread evenly over its months.

**Exports.** *PDF* (▾ *XLSX*) next to the title exports exactly what is
unfolded. The workbook holds real numbers with a number format, so it adds
up; thousands and millions are a format, not a rounding.

**Saved views.** *★ Views* saves the page: layout, period rule ("last month",
"financial year to date"), comparison, filters, display and unfolded rows.
A view can be shared with the team and made one's default. From *Schedule &
manage* an accountant has it emailed daily, weekly or monthly at an hour of
their time zone, as PDF and/or Excel, with the key lines in the email body.
The period is worked out at sending time, and the report is computed as the
view's owner: recipients never get more than the owner can see.

## How the figures are computed

* Only P&L accounts are counted: the account types `income`, `income_other`,
  `expense`, `expense_other`, `expense_depreciation` and
  `expense_direct_cost`. Archived accounts with postings still count.
* Journal items are read through the ORM's `_search`, so access rights and
  record rules apply: a user restricted to one company only sees that one.
  Selected companies must share a currency (consolidating different
  currencies is not in this version).
* Income lines show credits as positive, expense lines debits. Subtotals are
  formulas over the lines as displayed (`GP = REV - COS`).
* **The report checks itself.** The layout line marked *Net result* must equal
  income minus expenses of every P&L account. Accounts no line covers appear
  on an *Unallocated accounts* line, accounts counted twice are listed, and any
  difference is shown above the report.
* Analytic proration: an item counts for the sum of the distribution
  percentages whose key contains a selected analytic account. Keys that
  combine plans ("12,15") count for each of their accounts. Splitting by a
  plan shows each account's share and a *Not assigned* part for what no
  account of the plan covers.
* Budgets are entered per account and month; a month counts in every column
  that overlaps it.

## Layouts

*Accounting → Configuration → P&L Layouts*. A line either adds up the accounts
matching a domain on accounts (types, tags, code prefixes, anything the
domain editor offers), or is a formula on other lines' codes using
`+ - * /`, brackets and numbers. Formulas are parsed, never evaluated as code.

The default layout:

| Code | Line | Content |
|---|---|---|
| REV | Revenue | `income` |
| COS | Cost of Revenue | `expense_direct_cost` |
| GP | **Gross Profit** | `REV - COS` |
| OPEX | Operating Expenses | `expense` |
| DEP | Depreciation | `expense_depreciation` |
| OP | **Operating Profit** | `GP - OPEX - DEP` |
| OIN | Other Income | `income_other` |
| OEX | Other Expenses | `expense_other` |
| NET | **Net Profit** | `OP + OIN - OEX` (net result) |

## Who can do what

| Group (Accounting) | P&L |
|---|---|
| Show Accounting Features - Readonly | Opens the report, unfolds, exports, saves and shares views |
| Show Full Accounting Features (accountant) | Also keeps budgets and annotations, schedules emailed views |
| Administrator | Also edits layouts |

Invoicing-only users do not get the report: it shows the whole ledger.

## Not in this version

Cash basis, Split Horizontally (a Balance Sheet layout), tax units and
consolidation across currencies.

## Tests

```
odoo-bin -d test -i odin_account_pnl --test-tags /odin_account_pnl --stop-after-init
```
