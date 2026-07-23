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
  (~1000-point bucket, ~50 pts/s restore) -> read ``extensions.cost`` on every
  response and sleep when ``currentlyAvailable < requestedQueryCost``.
- Webhooks: Shopify retries ~19 times over 48h then DELETES the subscription;
  :func:`cron_heal_webhooks` re-registers missing topics daily (self-healing).
"""
import logging
import time

import requests

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

#: Single pinned Shopify Admin API version for the whole module.
#: Verified current stable on 2026-07-23 (release notes:
#: https://shopify.dev/docs/api/release-notes). Sunset: 2027-07-01.
API_VERSION = "2026-07"

#: Topics the connector needs for real-time two-way sync. The webhook
#: controller maps them to job kinds; the heal cron re-registers missing ones.
WEBHOOK_TOPICS = (
    "orders/create",
    "orders/updated",
    "orders/cancelled",
    "products/create",
    "products/update",
    "inventory_levels/update",
    "customers/update",
    "refunds/create",
)

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
        required=True, tracking=True,
        help="myshopify domain, e.g. mystore.myshopify.com (no scheme).")
    access_token = fields.Char(
        required=True, groups="shopify_bisync.group_connector_admin")
    webhook_secret = fields.Char(
        groups="shopify_bisync.group_connector_admin",
        help="App's API secret used to verify webhook HMAC signatures.")
    company_id = fields.Many2one(
        "res.company", required=True, index=True,
        default=lambda self: self.env.company)
    warehouse_id = fields.Many2one(
        "stock.warehouse", required=True, check_company=True,
        help="Default warehouse for orders and the stock source when no "
             "location mapping line matches.")
    pricelist_id = fields.Many2one(
        "product.pricelist", check_company=True,
        help="Prices exported to Shopify are computed from this pricelist "
             "(fallback: product sales price).")
    admin_user_id = fields.Many2one(
        "res.users", string="Connector Admin",
        default=lambda self: self.env.user,
        help="Receives escalation activities: quarantined jobs, risky orders, "
             "edits on delivered orders.")

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
        [("off", "Off"), ("export", "Odoo → Shopify")],
        default="export", required=True)
    sync_orders = fields.Selection(
        [("off", "Off"), ("import", "Shopify → Odoo")],
        default="import", required=True)
    conflict_policy = fields.Selection(
        [("odoo_wins", "Odoo wins"), ("shopify_wins", "Shopify wins"),
         ("newest_wins", "Most recent edit wins")],
        default="newest_wins", required=True, tracking=True,
        help="Applied when the same record changed on both sides between "
             "syncs. Every resolution is logged on the record's chatter.")
    auto_export_new_products = fields.Boolean(
        help="Export products to Shopify as soon as they are created in Odoo "
             "(otherwise only already-bound products are kept in sync).")
    export_status_default = fields.Selection(
        [("ACTIVE", "Active"), ("DRAFT", "Draft")], default="DRAFT",
        required=True, string="New Product Status",
        help="Shopify status given to products first exported from Odoo.")
    compare_at_policy = fields.Selection(
        [("off", "Do not send"), ("list_price", "Sales price as compare-at")],
        default="off", required=True, string="Compare-at Price",
        help="Optional mapping: when the pricelist price is below the "
             "product sales price, send the sales price as compare-at.")

    # ---------------------------------------------- order import policies ---
    tax_policy = fields.Selection(
        [("odoo", "Odoo computes taxes (fiscal positions)"),
         ("shopify", "Shopify amounts win (exact totals)")],
        default="odoo", required=True,
        help="Odoo mode: lines carry Odoo taxes, Shopify tax lines are kept "
             "as a note. Shopify mode: lines carry no tax and adjustment "
             "lines force the order total to match Shopify to the cent.")
    discount_policy = fields.Selection(
        [("line", "Per-line discount %"),
         ("separate", "Single negative discount line")],
        default="line", required=True)
    confirm_policy = fields.Selection(
        [("draft", "Always import as quotation"),
         ("confirm", "Confirm when paid / partially paid"),
         ("confirm_invoice", "Confirm + invoice + register payment")],
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

    # ------------------------------------------------------------ mappings --
    location_map_ids = fields.One2many(
        "shopify.bisync.location.map", "instance_id", string="Locations")
    carrier_map_ids = fields.One2many(
        "shopify.bisync.carrier.map", "instance_id", string="Carriers")

    last_import_orders = fields.Datetime(readonly=True)
    last_export_stock = fields.Datetime(readonly=True)
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
        """Raise on the ``userErrors`` array every mutation payload carries."""
        errors = (payload or {}).get("userErrors") or (
            (payload or {}).get("mediaUserErrors") or [])
        if errors:
            raise UserError(_(
                "Shopify %(mutation)s rejected: %(msg)s", mutation=mutation,
                msg="; ".join(
                    f"{'.'.join(map(str, e.get('field') or []))}: {e.get('message')}"
                    for e in errors)[:500]))

    # -------------------------------------------------------------- actions -
    def action_test_connection(self):
        self.ensure_one()
        shop = self.api_call("GET", "shop.json").get("shop", {})
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "sticky": False,
                       "message": _("Connected to %(shop)s (plan: %(plan)s).",
                                    shop=shop.get("name", self.shop_url),
                                    plan=shop.get("plan_name", "?"))}}

    def action_register_webhooks(self):
        """Create/repair the inbound webhooks (idempotent, also run daily by
        :func:`cron_heal_webhooks` because Shopify deletes subscriptions that
        keep failing)."""
        base = self.get_base_url()
        for instance in self:
            if base.startswith("http://"):
                raise UserError(_(
                    "Shopify only delivers webhooks to HTTPS endpoints. Set "
                    "web.base.url to your public https URL first."))
            existing = instance.api_call("GET", "webhooks.json", params={
                "limit": 250})["webhooks"]
            have = {(w["topic"], w["address"]) for w in existing}
            address = f"{base}/shopify_bisync/webhook/{instance.id}"
            for topic in WEBHOOK_TOPICS:
                if (topic, address) not in have:
                    instance.api_call("POST", "webhooks.json", {
                        "webhook": {"topic": topic, "address": address,
                                    "format": "json"}})
            instance.message_post(body=_("Webhooks verified/registered (%s topics).",
                                         len(WEBHOOK_TOPICS)))
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
