# Shopify Connector — Two-Way Sync · User Guide

Odoo 19 · License OPL-1 · Support: developers@fleet.ke

## 1. Setup (≈10 minutes)

1. **Create a custom app in Shopify**: *Settings → Apps and sales channels →
   Develop apps → Create an app*. Grant Admin API scopes:
   `read_products, write_products, read_inventory, write_inventory,
   read_orders, write_orders (for cancellations), read_customers,
   read_fulfillments, write_fulfillments, read_locations, write_publications`.
   Install the app and copy the **Admin API access token** and the **API
   secret key**.
2. **In Odoo**: *Shopify Sync → Configuration → Stores → New*. Fill shop URL
   (`mystore.myshopify.com`), access token, webhook secret (= API secret
   key), company, warehouse. **Test Connection**.
3. **Register Webhooks** (requires a public HTTPS `web.base.url`). A daily
   cron re-registers anything Shopify drops — no maintenance needed.
4. **Fetch Locations** and map each Shopify location to an Odoo warehouse on
   the *Locations* tab. Unmapped locations are ignored by stock sync.
5. Run **Backfill** (menu *Shopify Sync → Backfill*): tick entities, use
   **Count (dry run)** first, then **Start**. Progress is visible under
   *Sync Jobs*; the import survives worker restarts and always yields to
   live webhooks.

## 2. Per-entity behavior

| Entity | Direction | Notes |
|---|---|---|
| Products | configurable, default two-way | Options/variants mirrored both ways; images (main image), status active/draft/archived ↔ Odoo active, SKU, barcode, weight, HS code (when the field exists). New-from-Odoo products get the *New Product Status* (draft by default). |
| Prices | export | Computed from the store's pricelist (fallback: sales price). Optional compare-at: sales price is sent as compare-at when the pricelist price is lower. |
| Inventory | export (two-way optional) | Source is **free quantity** per mapped warehouse, pushed absolutely via GraphQL. Two-way mode applies Shopify levels as inventory adjustments. |
| Customers | import | Deduplicated by email; billing/shipping become child addresses; country/state resolved by ISO code. Marketing consent is never synced. |
| Orders | import | See §3. |
| Fulfillments | export | Validating an outgoing delivery pushes a fulfillment (FulfillmentOrders API) with tracking ref + carrier. Two pickings → two fulfillments. |
| Refunds | import | Each Shopify refund becomes a **draft** credit note linked to the order's posted invoice. Nothing is ever auto-posted. |

## 3. Order import policies (per store)

- **Confirmation**: import as quotation / auto-confirm when paid or
  partially paid / auto-confirm + invoice + register payment on a journal
  of your choice (full payments only).
- **Taxes** — the important choice:
  - *Odoo computes* (default): your fiscal positions and product taxes
    apply. Shopify's tax lines are archived as a chatter note for
    reference.
  - *Shopify amounts win*: lines carry no Odoo tax; a "Shopify Taxes" line
    and, if needed, a one-cent-level "Shopify Rounding" line force the
    order total to equal Shopify's `total_price` exactly.
- **Discounts**: per-line percentage, or one negative "Shopify Discount"
  line.
- **Shipping**: shipping lines map to delivery methods via the *Carriers*
  tab (matched on Shopify code, then title), with a fallback carrier.
- **Unmatched lines**: resolution ladder is variant binding → SKU → barcode
  → fallback product. Fallback hits are flagged `[UNMATCHED]` on the line
  and recorded in *Mismatch Log* — lines are never dropped.
- **Risky orders**: when Shopify recommends cancellation, the order is
  tagged "Shopify: Risky" and your connector admin gets an activity.

## 4. Order edits & cancellations

Quantity changes on lines that have **not** been delivered are applied and
summarized in the chatter. Any edit touching a delivered/locked line only
creates a review activity — the connector never silently mutates shipped
orders. Cancellations cancel the Odoo order while nothing has shipped;
otherwise an activity asks a human to handle the return.

## 5. The conflict engine

Every binding stores two fingerprints: Shopify's `updated_at` and Odoo's
`write_date` at the last successful sync. When an inbound change arrives for
a record that Odoo *also* changed, the store's policy decides:

- **Odoo wins** — inbound revision discarded, Odoo re-exports;
- **Shopify wins** — inbound applied;
- **Most recent edit wins** — timestamps compared.

Every resolution posts a chatter message: *"Shopify sync conflict on
name, description: kept Shopify version (policy: newest_wins)."* Unchanged
records are recognized by checksum (covering **all** exported fields,
images included) and produce **zero** API calls.

## 6. Operations

- **Sync Jobs**: everything flows through a durable queue. Failures retry
  with exponential backoff (2/4/8 minutes); after 4 attempts a job is
  quarantined (*Failed*) and the connector admin gets one activity. Use
  **Retry now** on a job or **Requeue all failed** from the list's action
  menu. Done jobs older than 30 days are vacuumed automatically.
- **Mismatch Log**: the "nothing is silent" ledger — unmatched lines,
  refunds without invoices, unmapped currencies, blocked edits.
- **Sunset watch**: the pinned Shopify API version is checked at every
  server start and daily; a log warning appears two quarters before
  sunset.

## 7. Uninstall

The uninstall hook best-effort deletes the webhooks the module registered
on every store, so a removed database stops receiving deliveries.

## 8. Out of scope (v1)

Shopify Plus B2B catalogs, Shopify Markets multi-currency price lists,
metafields (v1.1 candidate), draft-order creation from Odoo, subscription
products, POS-location nuances beyond the location mapping.
