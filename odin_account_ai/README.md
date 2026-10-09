# Accounting AI (`odin_account_ai`)

An *AI* menu in Accounting, built on Claude (Anthropic), in the style of the
AI-native ledgers: ask the books a question, see why a figure moved, clear
uncategorised transactions, catch what looks wrong, and draft journal entries
from a sentence. The AI proposes; an accountant decides. Nothing is posted or
changed in the books until someone with accounting rights presses a button.

Odoo 19 Community. Depends on `odin_account_pnl` (the interactive P&L) and
`mail`, and on the official `anthropic` Python package.

## The menu

*Invoicing/Accounting → AI*

| Screen | What it does |
|---|---|
| **Ask** | Questions in plain words ("How did revenue last month compare with the month before, and who drove the change?"). The answer comes from the ledger through read-only tools, as text, figures, tables and key numbers. Every figure carries a reference chip (`E3`) that opens the P&L at that row or the document itself. Follow-up questions continue the same conversation; past questions are in *History*. |
| **Transaction Review** | Bank lines still on the suspense account and draft bill lines still on the purchase journal's default account, each with a proposed income or expense account, grouped *Ready to accept*, *Worth a look*, *Needs you* and *Reconcile instead*. J/K to move, A to accept, R to reject; the account and partner can be changed before accepting; *Always categorise this partner like this* makes a rule. |
| **Anomalies** | Seven checks run every night (and on *Run checks now*): possible duplicate bills, unusual bill amounts, an account new for a supplier, large manual entries on cash/receivables/payables/equity, backdated entries, income and expenses without analytic, and bank lines left unreconciled. Findings by severity, with their entries, an owner, a deadline, chatter and activities. *Explain with AI* suggests the likely cause and the next step. |
| **Draft Entry** | Describe the entry ("Accrue 120,000 audit fees for September, to reverse on 1 October"); the AI proposes the lines from your chart of accounts and says what it assumed. The lines stay editable, and a banner lists what blocks the entry. *Create draft entry* or *Post and schedule reversal*. |
| Categorisation Rules, Anomaly Checks, Activity Log, Settings | Rules made from Review; the checks' thresholds; every call to the model with its tokens (AI managers). |

**Explain** is in the P&L itself: *⋯ → Explain this change* on a line or an
account (or the `e` key) opens a side-panel tab. It splits the change against
the previous period (or the comparison on screen) into what moved it: the
accounts of a line, the partners of an account, or for a subtotal such as
Gross Profit the lines it is built from. Those figures are computed, not
generated, and always add up to the change, with or without the AI. The AI
then writes a short summary that can only cite them.

## How Transaction Review decides

In this order, for each item:

1. **An open invoice or bill paid by it.** A bank line with the same amount
   as an open receivable or payable (and its reference, or the same partner)
   is sent to *Reconcile instead*, never booked to an account, or it would be
   counted twice. So is a transfer between two of the company's own bank or
   cash journals (same amount the other way within three days).
2. **Your rules.** A rule says that a partner's money in or out goes to an
   account (and analytic distribution).
3. **A partner with unpaid invoices or bills.** Money from a customer with
   unpaid invoices, or to a supplier with unpaid bills, goes to *Reconcile
   instead* even when no single document matches the amount.
4. **Claude**, 20 items per call, with each partner's history (where its past
   items were booked) and similar past labels. Only income and expense
   accounts of the company can be proposed; anything else is ignored. The
   model's confidence counts above 70% only when the books agree with it.

Accepting a bank suggestion rebuilds the statement line's entry the way
Odoo's own *undo reconciliation* does, with the chosen account instead of
suspense, under a row lock, and only if the line is unchanged since the
suggestion, still has one liquidity and one suspense line, is not reconciled
or secured (hashed) and is not in a locked period. Otherwise the suggestion
is marked *Changed since* or shows why it was refused. With the OCA bank
reconciliation (`account_reconcile_oca`), a line someone started reconciling
there, and journals in *keep suspense* mode, are left alone. Taxes are not
applied to bank lines. A bill suggestion changes the draft line's account;
the bill stays a draft. Every booking leaves a note in the document's chatter.

## The AI and your data

* **Consent first.** Nothing is sent until an administrator ticks *Send
  accounting data to Anthropic* for the company (*Settings → Accounting AI*).
* **What is sent:** the question or the item, and what the tools read for it
  (P&L figures, journal items, account and partner names), as JSON data. The
  system prompt tells the model that text from the books is data, never
  instructions.
* **What the model can do:** read, through five tools (P&L, splits, journal
  items, one entry, look up a name) that run with the asking user's access
  rights and record rules, at most 50 rows each. It has no tool that writes.
  Every id and code it returns is checked here; references it invents are
  dropped; answers are shown as structured blocks, never as HTML.
* **Budget:** a monthly token budget per company (3 million by default).
* **Log:** every call is in *Activity Log* with its tokens, duration and
  outcome. Responses are erased after the retention period (90 days by
  default); the log line stays.
* **Key:** the API key is a system parameter only administrators can read;
  the server variable `ANTHROPIC_API_KEY`, when set, takes precedence so the
  key can stay out of the database.

## How it calls Claude

The official SDK's Messages API (`client.beta.messages.create`), with:

* model `claude-opus-5-5` by default (one for Transaction Review, one for the
  rest, both in Settings);
* structured outputs (`output_config.format` with a JSON schema) for every
  answer, and the effort set per feature (low for Explain and Review, medium
  for Ask, Anomalies and Draft);
* the system prompt cached (`cache_control`), tools in a fixed order;
* server-side fallbacks (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`) except for Haiku models, which have
  none; after a fallback, the turn is echoed back as the API requires;
* refusals and answers cut short reported to the user, never used.

Odoo stops a request after `limit_time_real` (120 s by default), so
interactive calls time out after 75 s without SDK retries, and Ask runs one
model call per request, the page stepping through them ("Reading the P&L for
Sep 2026", "Splitting by partner"). A request Odoo retries (serialization
failure) reuses the stored answer instead of paying twice. The nightly
Transaction Review job takes one batch per company per run and reports its
progress so Odoo's cron runner calls it again while items remain.

Documentation: [Messages and tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview),
[structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
[prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[refusals and fallbacks](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback).

## Install

1. `pip install anthropic==1.8.0` in the server's Python (pinned in the
   repository's `requirements.txt`), then restart Odoo.
2. Install *Accounting AI*.
3. *Invoicing/Accounting → Configuration → Settings → Accounting AI*: tick the
   consent, paste the API key, *Test connection*.

Explain's computed drivers, rules, open-item matching and the anomaly checks
work without a key.

## Who can do what

| Group | AI |
|---|---|
| Accounting AI / User (given to accountants) | Uses the AI screens within their own accounting rights |
| Show Accounting Features - Readonly | Reads Ask, Explain, Review and Anomalies (with AI / User) |
| Show Full Accounting Features (accountant) | Also books suggestions, makes rules, works findings, drafts and posts entries |
| Accounting AI / Manager (given to administrators) | Also sees every AI call and edits the checks |
| Settings administrator | Consent, key, models, budget |

Each method checks the accounting group itself: posting an entry in Odoo only
needs Invoicing rights, so the AI's actions ask for the accountant group.

## Not in this version

Reconciling from Review (it points to the bank screen), label-pattern rules,
a second approver, the Batches API for the nightly job, streaming answers.

## Tests

The API is never called by the tests: they replace the one network call with
responses shaped like the API's, so request building, logging, budget and the
validation of answers run for real.

```
odoo-bin -d test -i odin_account_ai --test-tags /odin_account_ai --stop-after-init
```
