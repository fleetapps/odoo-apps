==================
Odoo Shopify Sync
==================

Bidirectional Shopify connector for products, stock, prices, customers, orders,
fulfillments and refunds. Inbound changes arrive over Shopify webhooks into a
durable job queue; outbound changes flow through Shopify's GraphQL Admin API.
Each entity has an explicit sync direction, and any record edited on both
sides between syncs is resolved by a conflict policy you choose — never
silently.

Live Demo
=========

A live instance is available for testing without installing anything:
https://sandbox.odin.ist/odoo/shopify-sync — log in with username ``admin``
and password ``admin``.

Installation
============

Download the module and add it to your Odoo addons folder. Log on to your
Odoo server, go to the Apps menu, enable developer mode and click
"Update Apps List". Search for "Odoo Shopify Sync" and click Install.

This module depends on ``sale_management``, ``stock``, ``delivery`` and
``account``, and requires the Python package ``requests`` to be available on
the server.

Access data for this OPL-1 module is provided at purchase. If you did not
purchase directly and need access, contact support (support@odin.ist) with a
confirmation of purchase.

Upgrade
=======

Download the new version, replace the module in your addons folder, restart
the server, then upgrade the module from the Apps menu. No manual data
migration is required between minor versions; the module's own upgrade
scripts handle schema changes.

Configuration
=============

Go to **Sales → Shopify → Stores** and add a store. Onboarding walks through
four steps:

1. **Connect your store** — approve the connection on Shopify's own consent
   screen. No API tokens to copy or paste.
2. **Test the connection** — confirms Shopify is answering before anything
   syncs.
3. **Turn on live updates** — webhooks are registered automatically once the
   store connects.
4. **Map your locations** — point each Shopify location at an Odoo warehouse.
   A location left unmapped is skipped, not guessed at.

From the store form you can then set, per entity (products, prices, inventory,
customers, orders, fulfillments, refunds):

* the sync direction (two-way, export-only or import-only, where applicable);
* the conflict policy for records edited on both sides — Odoo wins, Shopify
  wins, or newest wins;
* the tax policy for imported orders — Odoo recalculates from your fiscal
  positions, or Shopify's totals win to the cent;
* whether imported orders auto-confirm and auto-invoice.

Once configured, use **Import what's already there** to backfill existing
products, customers and orders: it counts first, then runs a resumable,
cursor-paginated import that survives a worker restart.

Usage
=====

After a store is connected and mapped, syncing is automatic — no user action
is required for day-to-day operation. Shopify webhooks push changes in as
they happen; Odoo edits are queued and sent out through the GraphQL Admin API.

Day-to-day monitoring happens under **Sales → Shopify**:

* **Sync Jobs** — the job queue: pending, running, retrying or quarantined
  (a job that fails four times stops retrying and notifies an admin).
* **Bindings** — the Odoo ↔ Shopify record pairs the connector is tracking.
* **Mismatches** — order lines that could not be matched to a product; each
  lands on a fallback product rather than being dropped.
* **Conflicts** — every conflict-policy resolution, logged for audit.
* **Sync Health** — a dashboard summarising what is syncing and what needs
  attention.
* **Payouts** — Shopify Payments stores only. Each payout's charges, refunds,
  fees and adjustments import and match to their source order and invoice;
  money only moves on the explicit Register Payments action.

Credits
=======

Author & Maintainer
--------------------

This module is maintained by `Odin <https://www.odin.ist>`_.

If you want to get in touch, contact support (support@odin.ist,
andrew@fleet.ke) or visit our website (https://www.odin.ist).
