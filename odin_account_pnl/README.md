# Interactive Profit & Loss (`odin_account_pnl`)

The Profit & Loss of Odoo 19 Enterprise, rebuilt for Community, on one page.
Odoo 19 Community has the `account.report` models but no engine to run them
and no P&L (both are Enterprise), so this module brings its own engine and a
default layout.

Odoo 19 Community. Depends on `account` and `mail`; no external Python.

## What the page does

*Accounting → Reporting → Statement Reports → Profit & Loss*

The page reads top down on white: the filters, the headline figures, then
the report in a card. **Headline figures** are the layout's base line
(Revenue), its bold subtotals and its net result, each as its name, its amount
and its change against the comparison (↗ green when it helps the profit, ↘ red
when not), with its margin on revenue, its budget and, with the 12-month trend
on, a sparkline; a card shows its line in the table.

The bar is one row: the period, then *label | value* controls (Compare |
Previous period, Budget | None, Detail | Lines), the value highlighted when it
is not the default. Compare and Budget carry an **×** that takes them off
again. **Filters** opens one panel for what narrows the journal items:
journals as toggles, partners and analytic accounts as pickers, posted or
draft entries. The filters in force show under the bar as chips, each with its
×, and *Clear all*.

| Control | What it does |
|---|---|
| 📅 Period | Last month by default, as Enterprise. This/last month, quarter, fiscal year (the company's fiscal year end), fiscal year to date or any dates. ◀ ▶ step to the previous or next period. *Divide into* months or quarters shows one column per period plus a total. |
| % Comparison | No comparison, previous period(s), same period last year(s), or custom, up to 12 periods. A growth % column turns green when the change is good for that line (more revenue, fewer costs). *Columns*: a **Year to date** column beside the period (from the fiscal year start, with its own budget), and, with several companies selected, **Companies side by side**: one column per company and their total, as Odoo's horizontal groups for a group's P&L. |
| Filters | Journals, partners, analytic accounts, and posted only or with drafts. The analytic filter **prorates**: a bill split 60/40 between two departments counts 60% in the first. |
| Detail | How far the whole report unfolds, on screen and in the PDF, the Excel and the scheduled emails: *Lines* (the summary), *Accounts* (every account under its line) or *Every transaction* (every journal item under its account, with totals: the "P&L detail" accountants ask for). |
| Budget | Adds Budget, Variance (actual minus budget, green when it helps the profit) and % achieved columns; amounts are typed into the report. |
| Display (⚙) | The layout (with more than one), units with or without decimals, thousands or millions, negatives in brackets; hide lines at 0, account codes, account groups, % of revenue, 12-month trend. |

Unfolding happens in place: a line opens on its accounts, an account on its
journal items, and every journal item opens its document (invoice, bill,
payment or entry) in the side panel. Rows are compact, or comfortable (the
toggle above the table, remembered per browser).

**Flux analysis.** With a comparison, every line, subtotal, account and split
gets a **Variance** (current minus the first comparison) and a **% Change**,
as pills that are green when the change helps the profit and red when not,
with their arrow and sign; both sort, by the size of the move. A change is
**material** when it is at least a percent *and* at least an amount: 10% and
1% of the period's revenue unless set otherwise (*Material ≥ …* above the
table; kept in saved views and their emails). Each row says whether it is
material, and the **Explanation** column shows its latest note, or
*+ Explain* on a material row that has none. The tabs above the table filter
the rows (with the lines they belong to, and whatever a kept row was unfolded
into):

| Tab | Rows |
|---|---|
| All lines | Everything, with its count |
| Material changes | The material ones |
| Needs explanation | Material, and no note yet: the month-end review's to-do list |
| With notes | The ones with notes |

Without a comparison, *Compare with the previous period* sits there instead.

**What's in this number.** Any line or account can be split, and a split row
split again (a partner by day, an order by product):

| By | |
|---|---|
| People | Partner, salesperson (of the invoice) |
| Documents | Invoice / bill / entry (named by its reference and contact), purchase order and sales order (when Purchase and Sales are installed; an accountant needs no purchase or sales rights to use them) |
| Products | Product, product category |
| Period | Day, week (from Monday), month, quarter: in order, every period |
| Journal, Analytic | Journal; an analytic plan's accounts, one plan at a time |

The ten largest contributors show with their share, the rest under *Others*;
periods all show, in order (up to a year of days). With a comparison, the
split shows who moved the number.

**Ledger.** An account can show its journal items oldest first, as the General
Ledger does, with their running balance in a *Balance* column (in the exports
too).

**Audit a cell.** Clicking a figure lists the journal items behind exactly that
figure (that row, that column), largest first.

**Side panel.** A journal item opens beside the report, not instead of it:
its document, lines with their analytic split, the attached PDF or image,
the last notes, and ◀ ▶ (or J/K) to step through the other items behind
the same figure. *Open* goes to the full form; the breadcrumb comes back to
the report exactly as it was (options, unfolded rows, panel, scroll).

**Finding your way.** A line's *By ▾* and *⋯* buttons show on the hovered or
focused line (always on touch screens), and a line that is split keeps its
*By* chip in view, named after the split (Partner, Ledger…). With the side
panel open the sparkline steps aside and the columns narrow, so the figures
and the growth stay on screen. A one-time tip explains the clicks, and the
keyboard button (or `?`) lists the shortcuts:

| Key | |
|---|---|
| ↑ ↓ | Move between lines |
| → ← | Unfold; fold, or go to the line it belongs to |
| Enter | Open the line, or show the journal item beside the report |
| J K | Next / previous journal item in the side panel |
| B | Split the line by partner, product, month… (↑ ↓ and Enter choose in the menu) |
| / | Find an account |
| Esc | Close the menu, then the side panel |
| ? | The shortcuts |

Shortcuts work while the report has the focus; Ctrl, Cmd and Alt combinations
are left to the browser and to Odoo (Ctrl+K stays the command palette).

**Notes.** *⋯ → Annotate* (or *+ Explain*) on a line, a subtotal or an
account. Notes show as a flag on the row, as the row's explanation with a
comparison, and as numbered footnotes in the PDF.

**Budget.** Pick or name a budget with the Budget chip: a Budget and a % column
appear, and an accountant types an account's budget straight into its cell. A
figure typed in a quarter or year column is spread evenly over its months.

**Exports.** *PDF* (▾ *Excel*) at the top right exports exactly what is
unfolded, at the chosen *Detail*, laid out as accountants read a detailed P&L
(Xero's and QuickBooks' detail reports): an unfolded account heads its
journal items, each with its date, contact and description, and its figure
moves to a *Total* row below them; journal items are all exported, not the
page on screen. The PDF opens on the headline figures, colours the
variance and % as on screen and marks material changes with "!" (the
thresholds in a legend below). The workbook holds real numbers and dates with a number
format, so it adds up (thousands and millions are a format, not a rounding);
its rows are grouped by level, so it folds back to the lines or the accounts
in Excel; and with journal items a second sheet, *Transactions*, lists them
flat with their line, account, journal and reference, ready for a pivot
table or a filter.

**Saved views.** *★ Views* saves the page: layout, period rule ("last month",
"financial year to date"), comparison, filters, display and unfolded rows.
The button names the view on the page (the default view included) and marks it
● once the page changes; the menu then offers to save the changes, revert to
the view as saved, or go back to the default P&L. Views are listed as *My
views* and *Shared with me*, each marked when it is emailed, paused or
failing; deleting one asks first.
A view can be shared with the team and made one's default.

**Schedule.** *Schedule*, beside *★ Views*, opens the schedule of the view on
screen (on a page that is not one of the user's views, a new view of the page
as it is, monthly to begin with). The dialog opens on where the schedule
stands (*Scheduled*, with the next email in the schedule's own time zone and
*Pause*; *Paused*, with *Resume*; or the last failure), then the name and what
the report shows, then four steps:

1. **When**: daily, weekdays (Monday to Friday), weekly on a day, monthly or
   quarterly (January, April, July, October) on a day, at an hour of the
   owner's time zone, said back in words.
2. **Report**: the period, worked out again for each email (Last month,
   This quarter…). Fixed dates are flagged, and a period that does not fit the
   frequency gets a tip: a weekly email of Last month sends the same month
   four times, a quarterly one of Last month leaves two months out.
   Attach the PDF and/or the Excel file, and the key figures in the email
   body (headline figures and the lines with their change, readable on a
   phone) or not.
3. **Recipients**: contacts and other addresses, each once; the dialog says
   who the email is from and where replies go.
4. **Message**: a subject (empty: "Profit & Loss · Oct 2026"; `{title}`,
   `{period}` and `{company}` are filled in) and a note. *Preview the email*
   shows it as the recipients will get it (from, to, subject, attachments,
   body) over the dialog, sending nothing.

*Save*; *Send me a test* sends the email to oneself, marked [Test], without
touching the schedule; *Send now* asks first, then sends it to the
recipients; *Pause* keeps the schedule as set up without sending. Every send is kept in the view's
**History** (when, scheduled / sent now / test, the outcome). Three scheduled
sends failing in a row pause the schedule, and the Schedule button and the
views list say so, rather than failing silently every period. The button
reads *Scheduled*, *Paused* or *Schedule failing* for the view on screen.

The email's *Open the P&L in Odoo* opens the view itself (`/odoo/pnl?pnl_view=<id>`;
someone who may not see that view gets the default P&L). It is sent from the
instance's own sending address, named after the company, with the view's
owner as Reply-To; an address on the instance's host name
(noreply@elora.odin.ist) is sent from the parent domain instead
(noreply@odin.ist), the one the mail relay accepts.
The period is worked out at sending time, and the report is computed as the
view's owner: recipients never get more than the owner can see.
*Accounting → Configuration → Saved P&L Views* lists every view with its
schedule, next email and last outcome (filters: emailed, paused, failing).

**Year-end review.** When only posted entries are counted, the report says how
many draft entries of the period it leaves out and what posting them would do
to the net profit (with the analytic share, under an analytic filter), with
*Review and post* (those entries, in the Journal Entries list, to post) and
*Include in the figures*. Appropriation of the profit (current year earnings accounts:
reserves, dividends) shows below Net Profit, as Odoo 19 presents it, without
changing it; only a layout line that names that account type reports them.

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
