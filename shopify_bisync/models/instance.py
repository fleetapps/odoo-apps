# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Shopify instance (one record per store) + the only API client in the module.

Auth: custom-app Admin access token (``X-Shopify-Access-Token`` header).

Platform facts (VERIFY-ON-BUILD register: see DOCS_REGISTER.md):
- API versions release quarterly and sunset after 12 months
  (https://shopify.dev/docs/api/usage/versioning). ``API_VERSION`` is the one
  pinned constant; :func:`api_version_sunset_warning` logs at startup (via the
  manifest ``post_load`` hook) once the pin is within 2 quarters of sunset.
- Products / variants / inventory MUST go through the GraphQL Admin API
  (``productSet``, ``productVariantsBulkUpdate``, ``inventorySetQuantities``).
  REST stays for webhook registration and orders/customers read.
- Rate limits (https://shopify.dev/docs/api/usage/limits): REST leaky bucket
  2 req/s burst 40 -> honour ``Retry-After`` on 429; GraphQL calculated cost
  (restore rate is plan-dependent: 100 pts/s Standard, 200 Advanced, 1000
  Plus, 2000 Enterprise) -> read ``extensions.cost`` on every response and
  sleep when ``currentlyAvailable < requestedQueryCost``. The rates are never
  hard-coded: ``restoreRate`` comes off the response, so a Plus store
  automatically drains its bigger bucket faster.
- Webhooks: Shopify retries ~19 times over 48h then DELETES the subscription;
  :func:`cron_heal_webhooks` re-registers missing topics daily (self-healing).
"""
import logging
import re
import secrets
import time
from urllib.parse import urlencode

import requests

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

#: Single pinned Shopify Admin API version for the whole module.
#: Re-verified as the current stable on 2026-08-04 against
#: https://shopify.dev/docs/api/usage/versioning (released 2026-07-01,
#: accessible until 2027-07-16 15:00 UTC). The warning below deliberately
#: computes the 1st of the month, i.e. it fires ~2 weeks early rather than
#: late.
API_VERSION = "2026-07"

#: Topics the connector needs for real-time two-way sync. The webhook
#: controller maps them to job kinds; the heal cron re-registers missing ones.
WEBHOOK_TOPICS = (
    "orders/create",
    "orders/updated",
    "orders/cancelled",
    "fulfillments/create",
    "fulfillments/update",
    "products/create",
    "products/update",
    "inventory_levels/update",
    "customers/update",
    "refunds/create",
    # Replaces the Order Risk API, deprecated in 2024-04. The payload shape is
    # not documented, so the handler only uses it as a trigger and re-reads
    # the assessment over GraphQL.
    "orders/risk_assessment_changed",
)

#: Scopes requested at install. Shopify shows these to the merchant on the
#: approval screen, so the list is exactly what the connector calls and no
#: more - anything extra reads as overreach on that screen and is one more
#: reason for a merchant to abandon the install.
#: read_all_orders is deliberately absent: it needs Shopify's approval and is
#: only required for orders older than 60 days.
OAUTH_SCOPES = ",".join((
    "read_products", "write_products",          # productSet, variants
    "read_inventory", "write_inventory",        # inventorySetQuantities
    "read_orders", "write_orders",              # order import/export
    "read_customers", "write_customers",        # customer sync
    "read_locations",                           # locations.json mapping
    "read_fulfillments", "write_fulfillments",  # fulfillmentCreate
    "read_merchant_managed_fulfillment_orders",
    "write_merchant_managed_fulfillment_orders",
    "read_assigned_fulfillment_orders",
    "write_assigned_fulfillment_orders",        # 3PL-fulfilled stores
    "read_shopify_payments_payouts",            # payout reconciliation
    # Sales-channel publishing: publishablePublish/Unpublish and the
    # publications query behind Fetch Sales Channels.
    "read_publications", "write_publications",
    # Product images go up via stagedUploadsCreate, which needs write_files
    # on top of write_products - without it image sync fails at upload.
    "write_files",
))

#: Shopify requires the shop parameter on the OAuth callback to match this
#: before anything else is trusted (authorization-code-grant docs).
SHOP_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com$")

#: Webhook subscriptions are managed over GraphQL. REST ``webhooks.json`` is
#: frozen legacy and does not know the newer topics - it answers 404 "Could
#: not find the webhook topic" for orders/risk_assessment_changed, which IS a
#: valid WebhookSubscriptionTopic on 2026-07. ``uri`` is the current field;
#: ``callbackUrl`` is deprecated.
WEBHOOK_LIST_QUERY = """
query { webhookSubscriptions(first: 250) {
  edges { node { id topic uri } }
} }"""

WEBHOOK_CREATE = """
mutation webhookSubscriptionCreate($topic: WebhookSubscriptionTopic!,
                                   $webhookSubscription: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
    webhookSubscription { id topic uri }
    userErrors { field message }
  }
}"""

GRAPHQL_MAX_RETRIES = 6
REST_MAX_RETRIES = 5


def api_version_sunset_warning():
    """Log a warning when the pinned version is within 2 quarters of sunset.

    Called from the manifest ``post_load`` hook so it runs once per server
    start, before any registry is loaded (plain logging only, no ORM).
    """
    year, month = (int(p) for p in API_VERSION.split("-"))
    sunset = fields.Date.to_date(f"{year + 1}-{month:02d}-01")
    warn_from = sunset - relativedelta(months=6)  # 2 quarters
    today = fields.Date.today()
    if today >= sunset:
        _logger.error(
            "shopify_bisync: pinned Shopify API version %s is PAST its sunset "
            "date (%s). Requests are being served the oldest supported "
            "version. Upgrade the module now.", API_VERSION, sunset)
    elif today >= warn_from:
        _logger.warning(
            "shopify_bisync: pinned Shopify API version %s sunsets on %s "
            "(less than 2 quarters away). Plan the version bump.",
            API_VERSION, sunset)


class ShopifyInstance(models.Model):
    _name = "shopify.bisync.instance"
    _description = "Shopify Store"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _check_company_auto = True

    _uniq_shop_company = models.Constraint(
        "UNIQUE(shop_url, company_id)",
        "This Shopify store is already connected for this company.")

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    shop_url = fields.Char(
        required=True, tracking=True, string="Store Address",
        help="Your store's permanent Shopify address, ending in "
             ".myshopify.com. In Shopify you'll find it under "
             "Settings > Domains, listed as the one you can't remove. Use "
             "that one here, not your own branded domain.")
    client_id = fields.Char(
        string="App Key", groups="shopify_bisync.group_connector_admin",
        help="From your Shopify app's Settings page, where it may also be "
             "called Client ID. It is not a password - it only identifies "
             "which app is asking. Paste the same one for every store you "
             "connect with this app.")
    client_secret = fields.Char(
        string="App Secret", groups="shopify_bisync.group_connector_admin",
        help="The matching secret from the same page. Treat it like a "
             "password: it is what proves the connection request really "
             "came from your Odoo, and it is also how Odoo checks that "
             "incoming updates genuinely came from Shopify.")
    access_token = fields.Char(
        readonly=True, copy=False,
        groups="shopify_bisync.group_connector_admin",
        help="Filled in automatically when you approve the connection in "
             "Shopify. There is nothing to type here.")
    # Drives every show/hide on the form. The form must NOT key its modifiers
    # off access_token directly: that field is groups-restricted, and
    # ir.ui.view._postprocess_access_rights *removes* restricted nodes for
    # non-members, leaving those modifiers pointing at a field that is no
    # longer in the arch. This one carries no groups, so it survives.
    is_connected = fields.Boolean(
        compute="_compute_is_connected", string="Connected",
        help="Whether this store has been approved in Shopify.")
    oauth_app_url = fields.Char(
        compute="_compute_oauth_redirect_uri", string="App URL",
        help="Shopify refuses the connection unless the app's own App URL is "
             "on the same host as the Redirect URL below, so it has to be set "
             "even though this connector never serves a page there.")
    oauth_redirect_uri_ok = fields.Boolean(
        compute="_compute_oauth_redirect_uri",
        help="False when this Odoo has no public https address configured, "
             "which makes the Redirect URL above wrong before it is even used.")
    oauth_redirect_uri = fields.Char(
        compute="_compute_oauth_redirect_uri", string="Redirect URL",
        help="Shopify will only send a merchant back to an address the app "
             "already knows. Copy this into your Shopify app's allowed "
             "redirection URLs, exactly as shown, before connecting.")
    oauth_state = fields.Char(
        readonly=True, copy=False,
        groups="shopify_bisync.group_connector_admin",
        help="One-time value that ties an approval in Shopify back to this "
             "store, so someone else's reply cannot be accepted in its place.")
    webhook_secret = fields.Char(
        groups="shopify_bisync.group_connector_admin",
        help="Set for you when you connect. Only fill this in by hand if "
             "you are using an older Shopify app that issued a separate "
             "signing secret.")
    company_id = fields.Many2one(
        "res.company", required=True, index=True,
        default=lambda self: self.env.company)
    warehouse_id = fields.Many2one(
        "stock.warehouse", required=True, check_company=True,
        string="Ships From",
        default=lambda self: self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1),
        help="Where online orders are fulfilled from, and whose stock levels "
             "are sent to Shopify. If you ship from more than one place, set "
             "this to your main one - you can match each Shopify location to "
             "its own warehouse later, on the Locations tab.")
    pricelist_id = fields.Many2one(
        "product.pricelist", check_company=True, string="Prices Come From",
        help="Which price list Odoo uses when it sends prices to Shopify. "
             "Leave empty to send each product's normal sales price.")
    admin_user_id = fields.Many2one(
        "res.users", string="Who To Notify",
        default=lambda self: self.env.user,
        help="The person told when something needs a human: an order Shopify "
             "flagged as risky, a change to an order that already shipped, or "
             "a sync that kept failing and was set aside.")

    # ------- two-way sync policy (the differentiator: direction per entity) --
    sync_products = fields.Selection(
        [("off", "Off"), ("import", "Shopify → Odoo"),
         ("export", "Odoo → Shopify"), ("both", "Two-way")],
        default="both", required=True)
    sync_stock = fields.Selection(
        [("off", "Off"), ("export", "Odoo → Shopify"),
         ("import", "Shopify → Odoo"), ("both", "Two-way")],
        default="export", required=True)
    sync_prices = fields.Selection(
        [("off", "Off"), ("export", "Odoo → Shopify"),
         ("import", "Shopify → Odoo"), ("both", "Two-way")],
        default="export", required=True,
        help="Two-way applies this store's conflict rule when a price changed "
             "on both sides, exactly as products do, and every resolution is "
             "recorded in the conflict ledger. Odoo keeps one sales price per "
             "product, so Shopify variants priced differently are imported at "
             "the first variant's price and the difference is logged.")
    sync_orders = fields.Selection(
        [("off", "Off"), ("import", "Shopify → Odoo")],
        default="import", required=True)
    stock_quantity_source = fields.Selection(
        [("free_qty", "Available to sell"), ("qty_available", "On hand"),
         ("virtual_available", "Forecasted")],
        default="free_qty", required=True, string="Stock To Send",
        help="Which Odoo number is sent to Shopify.\n"
             "Available to sell: on hand minus what is already reserved for "
             "other orders - the safest, and the default.\n"
             "On hand: everything physically in the warehouse, including "
             "units already promised to someone else.\n"
             "Forecasted: on hand plus what is on the way in. Only sensible "
             "if you are happy to sell stock you have not received.")
    stock_target_name = fields.Selection(
        [("available", "Available"), ("on_hand", "On hand")],
        default="available", required=True, string="Update In Shopify",
        help="Which of Shopify's own two figures to overwrite. Shopify "
             "accepts no others.")
    stock_skip_zero = fields.Boolean(
        string="Never Send Zero", default=False,
        help="Leave Shopify's number alone instead of setting it to zero when "
             "Odoo runs out. Useful if you sell the same stock somewhere else "
             "and do not want Odoo to take the product off sale - but it does "
             "mean Shopify can sell what you do not have.")
    export_only_in_stock = fields.Boolean(
        string="Only Export Products In Stock", default=False,
        help="Skip products with nothing available when exporting to Shopify. "
             "They are exported later, automatically, once stock arrives.")
    export_categories_as_collections = fields.Boolean(
        string="Categories As Collections", default=False,
        help="Create a Shopify collection for each Odoo product category and "
             "put exported products in the matching one.")
    conflict_policy = fields.Selection(
        [("odoo_wins", "Odoo wins"), ("shopify_wins", "Shopify wins"),
         ("newest_wins", "Most recent edit wins")],
        default="newest_wins", required=True, tracking=True,
        help="Applied when the same record changed on both sides between "
             "syncs. Every resolution is logged on the record's chatter.")
    product_match_policy = fields.Selection(
        [("off", "Always create new"),
         ("sku_barcode", "Match on SKU, then barcode"),
         ("sku_barcode_title", "Match on SKU, then barcode, then exact title")],
        default="sku_barcode_title", required=True,
        string="Match Before Create",
        help="Before creating a product on either side, look for one that is "
             "already the same product. This is what stops a first sync from "
             "doubling a catalogue that exists on both sides. A match is only "
             "adopted when it is UNAMBIGUOUS - two candidates means neither is "
             "used and the mismatch log records it for a human.")
    auto_export_new_products = fields.Boolean(
        help="Export products to Shopify as soon as they are created in Odoo "
             "(otherwise only already-bound products are kept in sync).")
    export_status_default = fields.Selection(
        [("ACTIVE", "Active"), ("DRAFT", "Draft")], default="DRAFT",
        required=True, string="New Product Status",
        help="Shopify status given to products first exported from Odoo.")
    compare_at_policy = fields.Selection(
        [("off", "Do not show a was-price"),
         ("list_price", "Show the Odoo sales price as the was-price")],
        default="off", required=True, string="Was-Price",
        help="Optional mapping: when the pricelist price is below the "
             "product sales price, send the sales price as compare-at.")

    # ---------------------------------------------- order import policies ---
    tax_policy = fields.Selection(
        [("odoo", "Let Odoo work out the tax"),
         ("shopify", "Use Shopify's figures exactly")],
        default="odoo", required=True,
        help="Odoo mode: lines carry Odoo taxes, Shopify tax lines are kept "
             "as a note. Shopify mode: lines carry no tax and adjustment "
             "lines force the order total to match Shopify to the cent.")
    discount_policy = fields.Selection(
        [("line", "As a discount % on each line"),
         ("separate", "As one discount line at the bottom")],
        default="line", required=True)
    invoice_policy = fields.Selection(
        [("all", "Every order"), ("paid", "Only once paid"),
         ("fulfilled", "Only once fulfilled")],
        default="all", required=True, string="Create Invoices For",
        help="Which imported orders get an Odoo invoice. Orders that do not "
             "qualify yet are invoiced automatically when they later become "
             "paid or fulfilled - nothing is missed, only deferred.")
    use_shopify_order_number = fields.Boolean(
        string="Use Shopify's Order Number", default=False,
        help="Name the Odoo order exactly as Shopify does, so the two match "
             "when someone reads a number down the phone. Leave off to use "
             "the prefix below instead, which is safer when several stores "
             "feed one company and their numbering can collide.")
    order_ref_prefix = fields.Char(
        default="SHOPIFY", required=True, string="Order Reference Prefix",
        help="Prefix of the imported order's Customer Reference, e.g. "
             "SHOPIFY/1001. Give each store its own prefix when several "
             "stores feed the same company, otherwise both show the same "
             "reference for their own order number 1001.")
    confirm_policy = fields.Selection(
        [("draft", "Leave as a quotation for someone to check"),
         ("confirm", "Confirm it once Shopify says it is paid"),
         ("confirm_invoice", "Confirm, invoice and mark it paid")],
        default="draft", required=True, string="Order Confirmation")
    payment_journal_id = fields.Many2one(
        "account.journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        help="Journal used to register payments in 'Confirm + invoice' mode.")
    fallback_product_id = fields.Many2one(
        "product.product", check_company=True, string="Unmatched Line Product",
        default=lambda self: self.env.ref(
            "shopify_bisync.product_unmatched", raise_if_not_found=False),
        help="Used when no product resolves for an order line "
             "(binding → SKU → barcode all failed). The line is never "
             "dropped; a mismatch log entry is created instead.")
    adjustment_product_id = fields.Many2one(
        "product.product", check_company=True, string="Adjustment Product",
        default=lambda self: self.env.ref(
            "shopify_bisync.product_adjustment", raise_if_not_found=False),
        help="Carries tax / rounding adjustment lines in 'Shopify amounts "
             "win' mode so totals match to the cent.")
    discount_product_id = fields.Many2one(
        "product.product", check_company=True, string="Discount Product",
        default=lambda self: self.env.ref(
            "shopify_bisync.product_discount", raise_if_not_found=False))
    fallback_carrier_id = fields.Many2one(
        "delivery.carrier", string="Fallback Carrier",
        default=lambda self: self.env.ref(
            "shopify_bisync.carrier_shopify_fallback",
            raise_if_not_found=False),
        help="Used when no carrier mapping line matches the Shopify "
             "shipping line code.")
    notify_customer_on_fulfillment = fields.Boolean(
        help="Ask Shopify to send its shipping-confirmation email when a "
             "fulfillment is pushed from Odoo.")
    import_fulfillment_status = fields.Boolean(
        default=True, string="Follow Shipping Status From Shopify",
        help="Reflect Shopify fulfillments in Odoo: confirm the order and "
             "validate the matching delivery so Odoo shows it shipped.")
    tip_product_id = fields.Many2one(
        "product.product", check_company=True, string="Tip Product",
        default=lambda self: self.env.ref(
            "shopify_bisync.product_tip", raise_if_not_found=False))
    duties_product_id = fields.Many2one(
        "product.product", check_company=True, string="Duties Product",
        default=lambda self: self.env.ref(
            "shopify_bisync.product_duties", raise_if_not_found=False))
    create_crm_lead = fields.Boolean(
        string="Create CRM Leads", default=False,
        help="Also open a CRM opportunity for each new Shopify customer. Only "
             "worth it if someone actually follows these up - otherwise it "
             "fills the pipeline with noise.")
    risk_policy = fields.Selection(
        [("off", "Do not check"),
         ("flag", "Tag risky orders and raise an activity")],
        default="flag", required=True, string="Fraud Risk",
        help="Read Shopify's risk assessment for each imported order. Costs "
             "one small extra GraphQL query per order. Orders Shopify "
             "recommends cancelling or investigating are tagged and raise an "
             "activity for the connector admin before anything ships.")
    itemize_taxes = fields.Boolean(
        string="Itemize Shopify Taxes",
        help="In 'Odoo computes' mode, also add each Shopify tax line "
             "(e.g. the Colorado Retail Delivery Fee) as its own named, "
             "tax-free line for exact destination-tax parity.")
    image_policy = fields.Selection(
        [("off", "Do not sync images"),
         ("primary", "Main image only"),
         ("gallery", "Full gallery")],
        default="gallery", required=True, string="Images",
        help="Full gallery makes Odoo authoritative for an exported product's "
             "images: Shopify's media is reconciled to match Odoo's, so an "
             "image added only in Shopify admin is removed on the next "
             "export. With two-way product sync that image reaches Odoo first "
             "(via the products/update webhook), so it survives. Extra images "
             "beyond the main one need the 'product.image' model, which ships "
             "with Odoo's website_sale - without it, only the main image "
             "syncs and the mismatch log says so once.")
    sync_product_tags = fields.Boolean(
        default=True, string="Sync Product Tags",
        help="Map Shopify product tags <-> Odoo product tags.")
    sync_product_category = fields.Boolean(
        default=True, string="Map Product Type ↔ Category",
        help="Map the Shopify product type <-> the Odoo product category.")

    # -------------------------------------- Odoo -> Shopify order updates ---
    push_paid_status = fields.Boolean(
        string="Tell Shopify When Paid",
        help="When an imported order's invoice is fully paid in Odoo, mark "
             "the Shopify order as paid (orderMarkAsPaid).")
    push_cancellations = fields.Boolean(
        string="Tell Shopify When Cancelled",
        help="When a Shopify-origin order is cancelled in Odoo, cancel it on "
             "Shopify too (orderCancel). Off by default - cancelling on "
             "Shopify can refund/restock.")
    cancel_restock = fields.Boolean(
        default=True, string="Restock on Cancel",
        help="Ask Shopify to restock items when pushing a cancellation.")
    refund_export_policy = fields.Selection(
        [("off", "Do not send refunds to Shopify"),
         ("record", "Record it in Shopify, without moving any money")],
        default="off", required=True, string="Send Refunds Back",
        help="When a credit note is posted in Odoo for a Shopify order, "
             "optionally record a matching refund on Shopify. Gateway money "
             "movement is intentionally left to the merchant in Shopify.")

    # ------------------------------------------------ publishing / channels -
    publish_policy = fields.Selection(
        [("manual", "I will publish them myself"),
         ("auto", "Put new products on sale automatically")],
        default="manual", required=True, string="Putting Products On Sale",
        help="Whether exporting a product also publishes it to the sales "
             "channels mapped below.")
    publication_ids = fields.One2many(
        "shopify.bisync.publication", "instance_id", string="Sales Channels")

    # --------------------------------------------------- scheduled import ---
    scheduled_import_orders = fields.Boolean(
        string="Also Check Regularly For Missed Orders",
        help="Belt-and-suspenders: a cron pulls orders updated since the last "
             "run, so a missed webhook never means a missed order.")

    # -------------------------------------------------- payouts (Shopify Pay)
    import_payouts = fields.Boolean(
        string="Import Payouts",
        help="Import Shopify Payments payout reports and match their "
             "transactions to Odoo invoices. Requires Shopify Payments.")
    payout_journal_id = fields.Many2one(
        "account.journal", check_company=True, string="Payout Journal",
        domain="[('type', 'in', ('bank', 'cash'))]",
        help="Clearing journal payout payments are registered against.")
    payout_auto_reconcile = fields.Boolean(
        string="Auto-register Payout Payments",
        help="On payout import, automatically register payment on matched, "
             "open invoices (otherwise use the manual button).")
    payout_fee_journal_id = fields.Many2one(
        "account.journal", check_company=True, string="Fee Journal",
        domain="[('type', '=', 'general')]",
        help="Miscellaneous journal the payout's fee entry is posted in.")
    payout_fee_account_id = fields.Many2one(
        "account.account", check_company=True, string="Fee Expense Account",
        help="Expense account for Shopify Payments processing fees. "
             "Registering payment on the invoices moves the GROSS amount into "
             "the clearing account while Shopify deposits the NET; this "
             "account is where the difference is booked so the clearing "
             "account empties and the bank line reconciles.")

    # ------------------------------------------------------------ mappings --
    location_map_ids = fields.One2many(
        "shopify.bisync.location.map", "instance_id", string="Locations")
    carrier_map_ids = fields.One2many(
        "shopify.bisync.carrier.map", "instance_id", string="Carriers")

    last_import_orders = fields.Datetime(readonly=True)
    last_export_stock = fields.Datetime(readonly=True)
    last_import_payouts = fields.Datetime(readonly=True)

    # Setup milestones. These exist so the onboarding panel and the header
    # buttons can reflect what has actually happened rather than guessing:
    # without them "connection tested" is unknowable after the notification
    # toast disappears.
    shop_currency = fields.Char(
        readonly=True, string="Store Currency",
        help="Read from Shopify when the connection is checked.")
    show_advanced = fields.Boolean(
        string="Show advanced settings", default=False,
        help="The settings below the line are ones most stores never change. "
             "They are hidden by default so the handful that matter are easy "
             "to find.")
    setup_warnings = fields.Html(
        compute="_compute_setup_warnings", sanitize=False,
        help="What still needs doing before this store syncs correctly.")
    connection_ok_on = fields.Datetime(
        readonly=True, copy=False,
        help="Last time Test Connection succeeded against this store.")
    webhooks_registered_on = fields.Datetime(
        readonly=True, copy=False,
        help="Last time the inbound webhooks were verified or registered.")
    job_count = fields.Integer(compute="_compute_job_count")

    # ------------------------------------------------------------------ ORM -
    @api.constrains("shop_url")
    def _check_shop_url(self):
        for instance in self:
            if "://" in (instance.shop_url or "") or "/" in (instance.shop_url or ""):
                raise ValidationError(_(
                    "Shop URL must be the bare myshopify domain, e.g. "
                    "mystore.myshopify.com (no scheme, no path)."))

    @api.onchange("shop_url")
    def _onchange_shop_url(self):
        if self.shop_url:
            self.shop_url = (self.shop_url.strip().lower()
                             .removeprefix("https://").removeprefix("http://")
                             .rstrip("/"))

    def _compute_job_count(self):
        counts = dict(self.env["shopify.bisync.job"]._read_group(
            [("instance_id", "in", self.ids)], ["instance_id"], ["__count"]))
        for instance in self:
            instance.job_count = counts.get(instance, 0)

    # ------------------------------------------------------------- helpers --
    @staticmethod
    def gid(resource, legacy_id):
        """Build a GraphQL GID from a numeric legacy id."""
        return f"gid://shopify/{resource}/{legacy_id}"

    @staticmethod
    def gid_to_id(gid):
        """``gid://shopify/Product/123`` -> ``'123'`` (str, keeps bindings
        REST/GraphQL agnostic: bindings always store the numeric id)."""
        return str(gid).rsplit("/", 1)[-1]

    def _base(self):
        return f"https://{self.shop_url}/admin/api/{API_VERSION}"

    def _headers(self):
        return {"X-Shopify-Access-Token": self.sudo().access_token,
                "Content-Type": "application/json"}

    # ---------------------------------------------------------- REST client -
    def api_call(self, method, endpoint, payload=None, params=None):
        """REST call with 429 leaky-bucket backoff (``Retry-After`` header).

        Returns ``(json_body, response_headers)``-style dict: the JSON body;
        cursor pagination callers use :func:`api_call_raw` to read ``Link``.
        """
        return self.api_call_raw(method, endpoint, payload, params)[0]

    def api_call_raw(self, method, endpoint, payload=None, params=None):
        """Like :func:`api_call` but also returns headers (``Link`` cursor)."""
        self.ensure_one()
        url = f"{self._base()}/{endpoint}"
        for _attempt in range(REST_MAX_RETRIES):
            resp = requests.request(
                method, url, json=payload, params=params,
                headers=self._headers(), timeout=30)
            if resp.status_code == 429:
                # Leaky bucket drained: Shopify tells us when to come back.
                time.sleep(min(float(resp.headers.get("Retry-After", "2")), 30))
                continue
            if resp.status_code >= 400:
                raise UserError(_(
                    "Shopify REST %(code)s on %(endpoint)s: %(body)s",
                    code=resp.status_code, endpoint=endpoint,
                    body=resp.text[:300]))
            return (resp.json() if resp.text else {}), resp.headers
        raise UserError(_("Shopify rate limit: REST retries exhausted."))

    # ------------------------------------------------------- GraphQL client -
    def graphql(self, query, variables=None):
        """Single cost-aware GraphQL entry point (Admin API).

        Reads the ``extensions.cost`` block on every response:
        - top-level ``THROTTLED`` error -> sleep exactly the point deficit
          (``(requested - available) / restoreRate``) and retry;
        - after a success, if ``currentlyAvailable < requestedQueryCost`` the
          next similar call would throttle -> sleep the deficit proactively.
        Raises :class:`UserError` on transport/GraphQL errors. Mutation
        callers must still check their payload's ``userErrors`` via
        :func:`check_user_errors`.
        """
        self.ensure_one()
        url = f"{self._base()}/graphql.json"
        body = {"query": query, "variables": variables or {}}
        for _attempt in range(GRAPHQL_MAX_RETRIES):
            resp = requests.post(url, json=body, headers=self._headers(),
                                 timeout=60)
            if resp.status_code == 429:  # rare on GraphQL but documented
                time.sleep(min(float(resp.headers.get("Retry-After", "2")), 30))
                continue
            if resp.status_code >= 400:
                raise UserError(_(
                    "Shopify GraphQL HTTP %(code)s: %(body)s",
                    code=resp.status_code, body=resp.text[:300]))
            doc = resp.json()
            cost = (doc.get("extensions") or {}).get("cost") or {}
            throttle = cost.get("throttleStatus") or {}
            errors = doc.get("errors")
            if errors:
                throttled = any(
                    (e.get("extensions") or {}).get("code") == "THROTTLED"
                    for e in errors)
                if throttled:
                    time.sleep(self._cost_deficit_sleep(cost, throttle))
                    continue
                raise UserError(_(
                    "Shopify GraphQL error: %(msg)s",
                    msg="; ".join(e.get("message", "?") for e in errors)[:300]))
            deficit = self._cost_deficit_sleep(cost, throttle, post_success=True)
            if deficit:
                time.sleep(deficit)
            return doc.get("data") or {}
        raise UserError(_("Shopify rate limit: GraphQL retries exhausted."))

    @staticmethod
    def _cost_deficit_sleep(cost, throttle, post_success=False):
        """Seconds to sleep so the bucket restores the current query's cost."""
        requested = float(cost.get("requestedQueryCost") or 50.0)
        available = float(throttle.get("currentlyAvailable") or 0.0)
        restore = float(throttle.get("restoreRate") or 50.0) or 50.0
        if post_success and available >= requested:
            return 0.0
        return max((requested - available) / restore, 0.5 if not post_success else 0.0)

    @staticmethod
    def check_user_errors(payload, mutation):
        """Raise on the userErrors array every mutation payload carries.

        Shopify names the array inconsistently (``userErrors``,
        ``mediaUserErrors``, ``orderCancelUserErrors``, ...), so collect any
        key ending in ``UserErrors``/``userErrors``."""
        errors = []
        for key, value in (payload or {}).items():
            if key == "userErrors" or key.endswith("UserErrors"):
                errors += value or []
        if errors:
            raise UserError(_(
                "Shopify %(mutation)s rejected: %(msg)s", mutation=mutation,
                msg="; ".join(
                    f"{'.'.join(map(str, e.get('field') or []))}: {e.get('message')}"
                    for e in errors)[:500]))

    # -------------------------------------------------------------- actions -
    # --------------------------------------------------------- setup checks -
    @api.depends("access_token", "webhooks_registered_on", "shop_currency",
                 "pricelist_id", "fallback_product_id", "location_map_ids",
                 "location_map_ids.warehouse_id", "sync_stock", "sync_orders")
    def _compute_setup_warnings(self):
        """Everything that will go wrong later, said once, before it does.

        Each of these used to be discovered the expensive way: an order lands
        with the wrong total, a line falls back to a placeholder product, or
        stock silently never leaves Odoo. All of them are knowable the moment
        the store is configured, so they belong here rather than in the
        mismatch log one affected record at a time.
        """
        for instance in self:
            items = []
            if not instance.is_connected:
                instance.setup_warnings = False
                continue  # the form already tells them to press Connect

            if not instance.webhooks_registered_on:
                items.append(_(
                    "<b>Live updates are not on yet.</b> Press "
                    "<i>Register Webhooks</i> above, or Shopify changes will "
                    "only reach Odoo on the next scheduled import."))

            if instance.sync_orders != "off" and instance.shop_currency:
                currency = instance.shop_currency
                if not self._pricelist_for_currency(instance, currency):
                    items.append(_(
                        "<b>No price list in %(cur)s.</b> Your Shopify store "
                        "sells in %(cur)s and Odoo has no price list in that "
                        "currency, so imported order totals will not match "
                        "Shopify. Create one under Sales > Products > Price "
                        "Lists.", cur=currency))

            if instance.sync_orders != "off" and not instance.fallback_product_id:
                items.append(_(
                    "<b>No fallback product.</b> If a Shopify order arrives "
                    "with a line Odoo cannot identify - a custom item, or a "
                    "product with no SKU - the import has nowhere to put it "
                    "and the order will fail instead of coming through."))

            if instance.sync_stock in ("export", "both"):
                mapped = instance.location_map_ids.filtered("warehouse_id")
                if not mapped:
                    items.append(_(
                        "<b>No Shopify location is linked to a warehouse.</b> "
                        "Stock will not leave Odoo until at least one is. "
                        "Press <i>Fetch Locations</i> above, then set the "
                        "warehouse on each row in the Locations tab."))

            instance.setup_warnings = (
                "<ul class='mb-0 ps-3'>"
                + "".join(f"<li class='mb-1'>{i}</li>" for i in items)
                + "</ul>") if items else False

    @api.model
    def _pricelist_for_currency(self, instance, currency_code):
        if (instance.pricelist_id
                and instance.pricelist_id.currency_id.name == currency_code):
            return instance.pricelist_id
        return self.env["product.pricelist"].search(
            [("currency_id.name", "=", currency_code),
             "|", ("company_id", "=", False),
             ("company_id", "=", instance.company_id.id)], limit=1)

    # ------------------------------------------------------------- OAuth ----
    @api.depends("access_token")
    def _compute_is_connected(self):
        # sudo: access_token is admin-only, but every user of the form needs
        # to know whether the store is connected in order to render it.
        for instance in self:
            instance.is_connected = bool(instance.sudo().access_token)

    @api.depends("shop_url")
    def _compute_oauth_redirect_uri(self):
        # Same value for every store; shown per record so it can be copied
        # from the screen where it is needed, without hunting for it.
        for instance in self:
            uri = instance._oauth_redirect_uri()
            instance.oauth_redirect_uri = uri
            instance.oauth_app_url = instance.get_base_url()
            # Catch the misconfiguration on the screen where the URL is copied,
            # rather than after the merchant has already pasted it in Shopify.
            instance.oauth_redirect_uri_ok = uri.startswith("https://")

    def _oauth_redirect_uri(self):
        """The one URL Shopify is allowed to send the merchant back to.

        Must be listed verbatim as an allowed redirection URL on the app, and
        must be https - Shopify refuses plain http.
        """
        return f"{self.get_base_url()}/shopify_bisync/oauth/callback"

    def action_connect_shopify(self):
        """Send the merchant to Shopify's approval screen.

        This is the whole connection flow from the merchant's side: they see
        Shopify's own consent page listing what the app may do, press Install,
        and land back here connected. Nothing is typed or pasted.

        Authorization code grant, offline access (``grant_options[]`` omitted
        and no ``expiring=1``), which returns a token with no expiry - so
        there is no refresh machinery to go wrong at 3am.
        https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/authorization-code-grant
        """
        self.ensure_one()
        if not self.client_id or not self.client_secret:
            raise UserError(_(
                "Add the App Key and App Secret from your Shopify app first, "
                "then press Connect to Shopify."))
        if not SHOP_DOMAIN_RE.match(self.shop_url or ""):
            raise UserError(_(
                "%(url)s is not a Shopify store address. It should look like "
                "your-store.myshopify.com - check Settings > Domains in "
                "Shopify for the address you cannot remove.",
                url=self.shop_url or ""))
        redirect_uri = self._oauth_redirect_uri()
        if not redirect_uri.startswith("https://"):
            raise UserError(_(
                "Shopify only sends merchants back to a secure (https) "
                "address, but this Odoo is configured as %(url)s. Set the "
                "web.base.url system parameter to your public https address "
                "and try again.", url=self.get_base_url()))
        # Tied to this record and checked on the way back, so a reply meant
        # for another store - or replayed by someone else - is rejected.
        # Held in a local rather than re-read: the value must be exactly the
        # one sent to Shopify, whatever happens to the record's cache next.
        state = secrets.token_urlsafe(32)
        self.sudo().oauth_state = state
        query = urlencode({
            "client_id": self.client_id,
            "scope": OAUTH_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        })
        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": f"https://{self.shop_url}/admin/oauth/authorize?{query}",
        }

    def action_test_connection(self):
        self.ensure_one()
        shop = self.api_call("GET", "shop.json").get("shop", {})
        # Only stamped on success: api_call raises before reaching this line.
        self.connection_ok_on = fields.Datetime.now()
        # Knowing the store's currency turns "totals will differ" from a
        # surprise found one order at a time into a setup warning shown once,
        # before a single order is imported.
        self.shop_currency = shop.get("currency") or False
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "sticky": False,
                       "message": _("Connected to %(shop)s (plan: %(plan)s).",
                                    shop=shop.get("name", self.shop_url),
                                    plan=shop.get("plan_name", "?"))}}

    def action_register_webhooks(self):
        """Create/repair the inbound webhooks (idempotent, also run daily by
        :func:`cron_heal_webhooks` because Shopify deletes subscriptions that
        keep failing).

        Registration goes through GraphQL, not REST ``webhooks.json``. The
        legacy REST endpoint never gained the newer topics and answers 404
        "Could not find the webhook topic" for ``orders/risk_assessment_changed``,
        even though it is a valid ``WebhookSubscriptionTopic`` - so the REST
        path could not register the full set at all.

        A topic Shopify refuses is recorded and skipped rather than aborting
        the run: one unavailable topic previously cost the store all its
        remaining subscriptions and left webhooks_registered_on unset, so the
        heal cron retried the same failure daily and never made progress.
        """
        failed = []
        for instance in self:
            if not instance._oauth_redirect_uri().startswith("https://"):
                raise UserError(_(
                    "Shopify only delivers webhooks to a secure (https) "
                    "address. Set the web.base.url system parameter to this "
                    "Odoo's public https address first."))
            address = f"{instance.get_base_url()}/shopify_bisync/webhook/{instance.id}"
            existing = instance.graphql(WEBHOOK_LIST_QUERY)
            have = {
                (edge["node"]["topic"], edge["node"]["uri"])
                for edge in ((existing.get("webhookSubscriptions") or {})
                             .get("edges") or [])
            }
            failed = []
            for topic in WEBHOOK_TOPICS:
                enum = topic.replace("/", "_").upper()
                if (enum, address) in have:
                    continue
                try:
                    data = instance.graphql(WEBHOOK_CREATE, {
                        "topic": enum,
                        "webhookSubscription": {"uri": address,
                                                "format": "JSON"}})
                    result = data.get("webhookSubscriptionCreate") or {}
                    instance.check_user_errors(result, "webhookSubscriptionCreate")
                except (UserError, ValidationError) as exc:
                    _logger.warning("shopify_bisync: topic %s refused on %s: %s",
                                    topic, instance.shop_url, exc)
                    failed.append((topic, str(exc)))

            # Stamped whenever the store has the subscriptions Shopify will
            # accept, so a permanently unavailable topic does not read as
            # "webhooks were never set up".
            instance.webhooks_registered_on = fields.Datetime.now()
            body = _("Live updates active: %(ok)s of %(total)s topics.",
                     ok=len(WEBHOOK_TOPICS) - len(failed),
                     total=len(WEBHOOK_TOPICS))
            if failed:
                body += "<br/>" + _(
                    "Shopify would not accept these, so the matching updates "
                    "will not arrive in real time:") + "<ul>" + "".join(
                        f"<li><b>{t}</b> — {e}</li>" for t, e in failed) + "</ul>"
            instance.message_post(body=body)

        if len(self) == 1 and failed:
            return {
                "type": "ir.actions.client", "tag": "display_notification",
                "params": {
                    "type": "warning", "sticky": True,
                    "title": _("Live updates partly set up"),
                    "message": _(
                        "%(ok)s of %(total)s topics are active. See the "
                        "store's message history for the ones Shopify "
                        "refused.", ok=len(WEBHOOK_TOPICS) - len(failed),
                        total=len(WEBHOOK_TOPICS)),
                }}
        return True

    def action_fetch_locations(self):
        """Pull Shopify locations into the mapping table (unmapped rows keep
        warehouse empty for the user to fill)."""
        LocationMap = self.env["shopify.bisync.location.map"]
        for instance in self:
            locations = instance.api_call("GET", "locations.json")["locations"]
            for loc in locations:
                if not LocationMap.search_count([
                        ("instance_id", "=", instance.id),
                        ("shopify_location_id", "=", str(loc["id"]))]):
                    LocationMap.create({
                        "instance_id": instance.id,
                        "shopify_location_id": str(loc["id"]),
                        "shopify_location_name": loc.get("name", ""),
                        "warehouse_id": instance.warehouse_id.id,
                    })
        return True

    def action_fetch_publications(self):
        """Pull Shopify sales channels (publications) into the mapping table."""
        Publication = self.env["shopify.bisync.publication"]
        query = ("query { publications(first: 50) { nodes { id name } } }")
        for instance in self:
            nodes = ((instance.graphql(query).get("publications") or {})
                     .get("nodes") or [])
            for node in nodes:
                pub_id = instance.gid_to_id(node["id"])
                if not Publication.search_count([
                        ("instance_id", "=", instance.id),
                        ("shopify_publication_id", "=", pub_id)]):
                    Publication.create({
                        "instance_id": instance.id,
                        "shopify_publication_id": pub_id,
                        "name": node.get("name", ""),
                    })
        return True

    def action_export_prices(self):
        """Queue a price export for every bound product of this store."""
        Job = self.env["shopify.bisync.job"]
        for instance in self:
            bindings = self.env["shopify.bisync.binding"].search([
                ("instance_id", "=", instance.id),
                ("res_model", "=", "product.template")])
            for binding in bindings:
                Job.enqueue(instance, "out", "price", {"res_id": binding.res_id},
                            priority=30, lock_key=f"price:{binding.res_id}")
        return True

    # -------------------------------------------------------- admin deep-link
    def admin_url(self, resource, external_id):
        """Classic admin URL (redirects to admin.shopify.com). resource e.g.
        'orders' / 'products'."""
        self.ensure_one()
        return f"https://{self.shop_url}/admin/{resource}/{external_id}"

    def action_open_jobs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "shopify_bisync.job_action")
        action["domain"] = [("instance_id", "=", self.id)]
        return action

    # ---------------------------------------------------------------- crons -
    @api.model
    def cron_heal_webhooks(self):
        """Daily: re-register any webhook Shopify dropped + re-log the API
        version sunset check inside the worker."""
        api_version_sunset_warning()
        for instance in self.search([]):
            try:
                instance.action_register_webhooks()
            except UserError as exc:
                _logger.warning("shopify_bisync: webhook heal failed for %s: %s",
                                instance.name, exc)

    @api.model
    def cron_pull_orders(self):
        """Belt-and-suspenders scheduled import: pull orders updated since the
        last successful pull and enqueue them (webhooks stay primary; this
        only backstops missed deliveries). REST orders read is allowed."""
        Job = self.env["shopify.bisync.job"]
        for instance in self.search([("sync_orders", "=", "import"),
                                    ("scheduled_import_orders", "=", True)]):
            params = {"status": "any", "limit": 100,
                      "updated_at_min": (
                          instance.last_import_orders
                          or fields.Datetime.now() - relativedelta(days=1)
                      ).isoformat() + "Z"}
            try:
                orders = instance.api_call(
                    "GET", "orders.json", params=params).get("orders", [])
            except UserError as exc:
                _logger.warning("shopify_bisync: scheduled pull failed for %s: %s",
                                instance.name, exc)
                continue
            for order in orders:
                order["_topic"] = "orders/updated"  # create-or-update semantics
                Job.enqueue(instance, "in", "order", order, priority=18,
                            lock_key=f"order:{order.get('id')}")
            instance.last_import_orders = fields.Datetime.now()

    def unregister_webhooks(self):
        """Best-effort webhook cleanup (uninstall hook / instance archive)."""
        for instance in self:
            try:
                hooks = instance.api_call("GET", "webhooks.json",
                                          params={"limit": 250})["webhooks"]
                marker = f"/shopify_bisync/webhook/{instance.id}"
                for hook in hooks:
                    if marker in (hook.get("address") or ""):
                        instance.api_call("DELETE", f"webhooks/{hook['id']}.json")
            except Exception:  # noqa: BLE001 - uninstall must never block
                _logger.warning("shopify_bisync: could not clean webhooks for %s",
                                instance.shop_url, exc_info=True)
