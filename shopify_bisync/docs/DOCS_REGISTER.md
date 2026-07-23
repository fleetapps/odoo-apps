# VERIFY-ON-BUILD register — shopify_bisync

Every platform assumption flagged in the scaffold, closed with the document
consulted and the date. Re-verify the Shopify rows at each quarterly API
version bump.

| # | Item | Decision baked into code | Doc consulted | Date |
|---|---|---|---|---|
| 1 | API version pin | `API_VERSION = "2026-07"` in `models/instance.py`; sunset 2027-07-01; startup + daily warning from 2 quarters out | <https://shopify.dev/docs/api/usage/versioning>, <https://shopify.dev/docs/api/release-notes> | 2026-07-23 |
| 2 | Products via GraphQL | `productSet` (synchronous) is the create/update path; REST product endpoints not used | <https://shopify.dev/docs/api/admin-graphql/latest/mutations/productSet> | 2026-07-23 |
| 3 | Variant prices | `productVariantsBulkUpdate` for price/compare-at; cannot carry inventory quantities (confirmed) | <https://shopify.dev/docs/api/admin-graphql/latest/mutations/productvariantsbulkupdate> | 2026-07-23 |
| 4 | Inventory absolute set | `inventorySetQuantities` with `name: "available"`, `ignoreCompareQuantity: true` | <https://shopify.dev/docs/api/admin-graphql/latest/mutations/inventorySetQuantities> | 2026-07-23 |
| 5 | Fulfillments | Legacy `/fulfillments.json` gone; `fulfillmentCreate` + `FulfillmentInput.lineItemsByFulfillmentOrder` targeting FulfillmentOrder line items (`fulfillmentCreateV2` deprecated name) | <https://shopify.dev/docs/api/admin-graphql/latest/mutations/fulfillmentcreate>, <https://shopify.dev/docs/api/admin-graphql/latest/input-objects/fulfillmentinput> | 2026-07-23 |
| 6 | Rate limits | REST leaky bucket (2 rps, burst 40): honour `Retry-After` on 429. GraphQL calculated cost: read `extensions.cost`, sleep `(requested - available)/restoreRate` on THROTTLED and proactively post-success | <https://shopify.dev/docs/api/usage/limits> | 2026-07-23 |
| 7 | Webhook lifecycle | ~19 retries over 48 h, then subscription deleted → answer 200 fast (enqueue only) + daily self-heal cron re-registering topics | <https://shopify.dev/docs/apps/build/webhooks> | 2026-07-23 |
| 8 | Webhook auth | HMAC-SHA256 base64 of the **raw body** vs app secret, constant-time compare, before any parsing (scaffold behavior kept verbatim) | <https://shopify.dev/docs/apps/build/webhooks/subscriptions/verify-webhooks> | 2026-07-23 |
| 9 | REST cursor pagination | `Link: <…page_info=…>; rel="next"`; when `page_info` is sent, other filters must be dropped | <https://shopify.dev/docs/api/usage/pagination-rest> | 2026-07-23 |
| 10 | Image export | No public Odoo URL required: `stagedUploadsCreate` → multipart POST → `productCreateMedia(originalSource)` | <https://shopify.dev/docs/api/admin-graphql/latest/mutations/stagedUploadsCreate> | 2026-07-23 |
| 11 | Odoo 19 SQL constraints | `_sql_constraints` no longer works in 19 → `models.Constraint` class attributes everywhere | <https://www.odoo.com/documentation/19.0/developer/reference/backend/orm/changelog.html> | 2026-07-23 |
| 12 | Odoo 19 view syntax | `<list>` roots, direct `invisible="expr"`, `<chatter/>` tag, `optional=` columns | <https://www.odoo.com/documentation/19.0/developer/reference/user_interface/view_architectures.html> | 2026-07-23 |
| 13 | Testing framework | `TransactionCase`/`HttpCase` + `unittest.mock` at the API-client boundary (no live HTTP in tests) | <https://www.odoo.com/documentation/19.0/developer/reference/backend/testing.html> | 2026-07-23 |
| 14 | Module hooks | `pre_init_hook(env)` / `uninstall_hook(env)` / `post_load()` signatures | <https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html> | 2026-07-23 |

## Items to re-verify on a live dev store before listing submission

- Exact `ProductSetInput` field set for the pinned version (notably `sku`
  placement on `ProductVariantSetInput` vs `inventoryItem.sku`) — the
  GraphQL schema is the source of truth; tests mock the transport.
- `risk_recommendation` availability on order webhook payloads for the
  pinned version (the field is optional in code).
- Odoo 17/18 backports: `is_storable` (19/18) vs `detailed_type` (17),
  `tax_ids` vs `tax_id` on sale.order.line (renamed in 18), stock context
  key `warehouse` vs `warehouse_id` (both are passed).
