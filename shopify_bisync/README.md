# Odoo Shopify Sync — Two-Way Connector

**Bidirectional Shopify sync for products, stock, prices, customers, orders,
fulfillments and refunds — webhook-driven inbound, queued outbound, and a
conflict engine that shows its work instead of silently picking a winner.**

Built on Shopify's GraphQL Admin API (`productSet`, `inventorySetQuantities` —
the API Shopify is committing its future to), with a durable job queue so a
Shopify rate-limit storm delays syncs, never loses them.

- **Odoo:** 19.0
- **License:** OPL-1
- **Depends:** `sale_management`, `stock`, `delivery`, `account`, `onboarding`
- **External Python dependency:** `requests`

---

## Table of contents
1. [Why this module](#why-this-module)
2. [What syncs, in which direction](#what-syncs-in-which-direction)
3. [Payout reconciliation](#payout-reconciliation)
4. [Installation](#installation)
5. [Connecting a store](#connecting-a-store)
6. [Configuration](#configuration)
7. [User guide](#user-guide)
8. [Conflict policy](#conflict-policy)
9. [Reliability model](#reliability-model)
10. [FAQ](#faq)
11. [Troubleshooting](#troubleshooting)
12. [Changelog](#changelog)

---

## Why this module

Most connectors pick a sync direction and hope your workflow matches it. This
one doesn't assume:

| Concern | How it's answered |
|---|---|
| *"What happens if the same product changes on both sides?"* | You choose the policy — Odoo wins, Shopify wins, or newest wins — and every resolution is written to the record's chatter. |
| *"What if Shopify sends an order line for a product we don't recognize?"* | It lands on a fallback product with a mismatch log. Nothing is dropped. |
| *"What if a sync job fails?"* | It backs off, retries, and quarantines after 4 attempts with an admin ping — instead of failing silently. |
| *"Will my order totals match Shopify's?"* | To the cent in "Shopify amounts win" mode; in "Odoo computes" mode your fiscal positions rule and Shopify's tax lines are archived for reference. |
| *"Does an edit ever touch an already-delivered order?"* | Never silently — it creates a review activity for a human instead. |

---

## What syncs, in which direction

| Entity | Direction | Highlights |
|---|---|---|
| **Products** | Two-way | Multi-variant options both ways, images, status (active/draft/archived), SKU, barcode, weight, HS code |
| **Prices** | Export | Pricelist per store, optional compare-at mapping |
| **Inventory** | Export (two-way optional) | Multi-location: Shopify locations ↔ Odoo warehouses mapping table, free qty as source |
| **Customers** | Import | Email dedup, address hierarchy, country/state resolution. Marketing consent never leaves Shopify. |
| **Orders** | Import | Tax policy (Odoo computes / Shopify totals to the cent), discounts, carrier mapping, auto-confirm & invoice gating, fraud flagging, order-edit diffing, safe cancellations |
| **Fulfillments** | Export | Modern FulfillmentOrders API, partial shipments per picking, tracking number + carrier |
| **Refunds** | Import | Draft credit note mirroring the refund — accounting reviews, nothing auto-posts |
| **Payouts** | Import | Shopify Payments only. Charges, refunds, fees and adjustments matched to the source order and invoice |

---

## Payout reconciliation

If a store uses **Shopify Payments** (not a third-party gateway), each payout
and its balance transactions — charges, refunds, fees, disputes,
adjustments — import automatically and are matched to the Odoo sale order
and posted invoice by Shopify order ID.

Money only moves on the explicit **Register Payments** action (or an opt-in
per-store auto flag) — never silently on import. On a store using a
different payment gateway, the payout API returns nothing and this feature
is simply inactive; nothing else is affected.

## Installation

1. Copy `shopify_bisync/` into your Odoo addons path.
2. Update the apps list and install **Odoo Shopify Sync** (Apps → search "Shopify").
3. Confirm **Settings → Technical → System Parameters → `web.base.url`** is your
   externally reachable HTTPS URL — Shopify webhooks and the OAuth callback are
   registered against it.

---

## Connecting a store

Sales → Shopify → **Stores** walks through a four-step onboarding flow:

1. **Connect your store** — add the store, then approve the connection on
   Shopify's own consent screen. No API tokens to copy.
2. **Test the connection** — confirms Shopify is answering before anything syncs.
3. **Turn on live updates** — webhooks are registered for you the moment the
   store connects.
4. **Map your locations** — point each Shopify location at an Odoo warehouse.
   Unmapped locations are skipped, not guessed at.

Then **Import what's already there**: a dry-run count first, then a resumable,
cursor-paginated backfill that survives worker restarts — 10,000-SKU catalogs
welcome.

---

## Configuration

Per store (Sales → Shopify → Stores → a store):

| Setting | Notes |
|---|---|
| Sync direction per entity | Two-way, export-only or import-only, per the table above |
| Conflict policy | Odoo wins / Shopify wins / newest wins |
| Location mapping | Shopify location → Odoo warehouse |
| Tax policy | Odoo computes vs. Shopify totals win |
| Auto-confirm / invoice gating | Whether imported orders confirm and invoice automatically |

Multi-store and multi-company are both native: each store is an independent
binding, and record rules scope everything per company.

---

## User guide

- **Sync Jobs** — the durable job queue: pending, running, retrying, quarantined.
- **Bindings** — the Odoo ↔ Shopify record pairs the connector is tracking.
- **Mismatches** — order lines that couldn't be matched to a product, with the
  fallback product they landed on.
- **Conflicts** — every conflict resolution, auditable after the fact.
- **Sync Health / Dashboard** — real-time view of what's syncing and what needs attention.

---

## Conflict policy

If the same record changes in Odoo and Shopify between syncs, your chosen
policy decides — and the decision is written to the record's chatter, e.g.
*"Conflict on name, price: kept Shopify version."* Nothing is resolved silently.

---

## Reliability model

- **Self-healing webhooks** — Shopify silently deletes failing subscriptions; a
  daily cron re-registers anything missing.
- **Rate-limit aware** — REST leaky-bucket backoff and GraphQL cost tracking on
  every call. A 429 storm delays syncs; it never loses them.
- **No phone-home, no CDN** — everything runs inside your Odoo. Your access
  token stays in your database, visible to connector admins only.

---

## FAQ

**Which Shopify plan do I need?**
Any plan that supports custom app installs (all current plans do).

**How do I connect a store — do I need to create a custom app and paste a token?**
No — connecting a store is OAuth: click *Connect*, approve on Shopify's own
consent screen, and you're done. No tokens to copy or paste.

**What happens when the same product is edited in both systems?**
Your chosen conflict policy decides, and the decision is written to the
product's chatter.

**Will imported order totals match Shopify?**
In "Shopify amounts win" mode, to the cent — tax and rounding adjustment lines
guarantee it. In "Odoo computes" mode your fiscal positions rule and Shopify's
tax lines are archived in a note.

**Does it modify delivered orders when Shopify edits them?**
Never silently. Un-delivered quantity changes are applied; anything touching
delivered lines creates a review activity for a human.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Store won't connect | `web.base.url` must be the external HTTPS URL — the OAuth callback and webhooks are registered against it. |
| Webhooks stopped arriving | Shopify silently drops failing subscriptions; wait for the daily re-registration cron or trigger it manually from the store form. |
| Order line landed on the wrong product | Check **Mismatches** — it's logged there with the fallback product used. |
| Sync job stuck | Check **Sync Jobs** for quarantined jobs (4 failed attempts); the failure reason is on the job record. |
| Totals don't match Shopify | Confirm the store's tax policy — "Odoo computes" intentionally recalculates rather than mirroring Shopify's totals. |

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) if present, or the module's version history on
the Odoo Apps Store listing.
