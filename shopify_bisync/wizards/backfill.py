# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Initial import / backfill (spec A4).

The wizard only SEEDS the first page job per entity; every page job then
fetches one page, enqueues one import job per record (jobs of <= 100
records) and enqueues the next page job carrying the cursor. Because the
cursor travels inside durable jobs, a worker kill resumes exactly where it
stopped - nothing depends on the transient wizard surviving.

Priorities: live webhooks (10-15) always outrank backfill pages (45) and
backfill items (50).

Cursors: GraphQL ``pageInfo.endCursor`` for products; REST ``Link:
rel="next"`` ``page_info`` for orders/customers (REST is acceptable for
orders/customers read; products MUST use GraphQL).
"""
import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

PAGE_PRIORITY = 45
ITEM_PRIORITY = 50
PAGE_SIZE = 100

PRODUCTS_COUNT = "query { productsCount { count } }"


def _rest_next_page_info(link_header):
    """Extract the ``page_info`` cursor of the rel="next" link, if any."""
    for part in (link_header or "").split(","):
        if 'rel="next"' in part:
            match = re.search(r"[?&]page_info=([^&>]+)", part)
            return match.group(1) if match else None
    return None


class BackfillWizard(models.TransientModel):
    _name = "shopify.bisync.backfill"
    _description = "Shopify Backfill / Initial Import"

    instance_id = fields.Many2one(
        "shopify.bisync.instance", required=True,
        default=lambda self: self.env["shopify.bisync.instance"].search(
            [], limit=1))
    do_products = fields.Boolean(default=True, string="Products")
    do_customers = fields.Boolean(string="Customers")
    do_orders = fields.Boolean(string="Orders")
    date_from = fields.Datetime(string="Orders From")
    date_to = fields.Datetime(string="Orders To")
    state = fields.Selection(
        [("draft", "Draft"), ("counted", "Counted"), ("queued", "Queued")],
        default="draft", readonly=True)
    count_products = fields.Integer(readonly=True)
    count_orders = fields.Integer(readonly=True)
    count_customers = fields.Integer(readonly=True)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for wizard in self:
            if (wizard.date_from and wizard.date_to
                    and wizard.date_from > wizard.date_to):
                raise UserError(_("'Orders From' must be before 'Orders To'."))

    def _order_params(self):
        params = {"status": "any"}
        if self.date_from:
            params["created_at_min"] = self.date_from.isoformat() + "Z"
        if self.date_to:
            params["created_at_max"] = self.date_to.isoformat() + "Z"
        return params

    def action_count(self):
        """Dry run: only counts, no job is enqueued."""
        self.ensure_one()
        instance = self.instance_id
        vals = {"state": "counted"}
        if self.do_products:
            data = instance.graphql(PRODUCTS_COUNT)
            vals["count_products"] = (data.get("productsCount") or {}).get(
                "count", 0)
        if self.do_orders:
            vals["count_orders"] = instance.api_call(
                "GET", "orders/count.json",
                params=self._order_params()).get("count", 0)
        if self.do_customers:
            vals["count_customers"] = instance.api_call(
                "GET", "customers/count.json").get("count", 0)
        self.write(vals)
        return {"type": "ir.actions.act_window", "res_model": self._name,
                "res_id": self.id, "view_mode": "form", "target": "new"}

    def action_start(self):
        self.ensure_one()
        Job = self.env["shopify.bisync.job"]
        instance = self.instance_id
        if self.do_products:
            Job.enqueue(instance, "in", "backfill",
                        {"entity": "product", "cursor": None,
                         "page_size": PAGE_SIZE},
                        priority=PAGE_PRIORITY, lock_key="backfill:product")
        if self.do_customers:
            Job.enqueue(instance, "in", "backfill",
                        {"entity": "customer", "page_info": None,
                         "page_size": PAGE_SIZE},
                        priority=PAGE_PRIORITY, lock_key="backfill:customer")
        if self.do_orders:
            Job.enqueue(instance, "in", "backfill",
                        {"entity": "order", "page_info": None,
                         "page_size": PAGE_SIZE,
                         "params": self._order_params()},
                        priority=PAGE_PRIORITY, lock_key="backfill:order")
        self.state = "queued"
        return instance.action_open_jobs()


class BackfillSync(models.AbstractModel):
    _name = "shopify.bisync.backfill.sync"
    _description = "Backfill Page Runner"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        entity = payload.get("entity")
        if entity == "product":
            self._page_products(job, payload)
        elif entity in ("order", "customer"):
            self._page_rest(job, payload)

    @api.model
    def _page_products(self, job, payload):
        from ..models.product_sync import PRODUCTS_PAGE
        instance = job.instance_id
        Job = self.env["shopify.bisync.job"]
        ProductSync = self.env["shopify.bisync.product.sync"]
        data = instance.graphql(PRODUCTS_PAGE, {
            "first": payload.get("page_size") or PAGE_SIZE,
            "after": payload.get("cursor")})
        products = data.get("products") or {}
        for node in products.get("nodes") or []:
            normalized = ProductSync._gql_to_dict(node)
            Job.enqueue(instance, "in", "product", normalized,
                        priority=ITEM_PRIORITY,
                        lock_key=f"product:{normalized['id']}")
        page_info = products.get("pageInfo") or {}
        if page_info.get("hasNextPage"):
            Job.enqueue(instance, "in", "backfill", {
                **payload, "cursor": page_info.get("endCursor")},
                priority=PAGE_PRIORITY, lock_key="backfill:product:next")

    @api.model
    def _page_rest(self, job, payload):
        instance = job.instance_id
        Job = self.env["shopify.bisync.job"]
        entity = payload["entity"]
        endpoint = "orders.json" if entity == "order" else "customers.json"
        params = {"limit": payload.get("page_size") or PAGE_SIZE}
        if payload.get("page_info"):
            # Shopify cursor pagination: page_info excludes other filters.
            params["page_info"] = payload["page_info"]
        elif entity == "order":
            params.update(payload.get("params") or {})
        body, headers = instance.api_call_raw("GET", endpoint, params=params)
        records = body.get("orders" if entity == "order" else "customers", [])
        for record in records:
            if entity == "order":
                record["_topic"] = "orders/create"
                lock = f"order:{record.get('id')}"
            else:
                lock = f"customer:{record.get('id')}"
            Job.enqueue(instance, "in", entity, record,
                        priority=ITEM_PRIORITY, lock_key=lock)
        next_page = _rest_next_page_info(headers.get("Link"))
        if next_page:
            Job.enqueue(instance, "in", "backfill", {
                **payload, "page_info": next_page},
                priority=PAGE_PRIORITY, lock_key=f"backfill:{entity}:next")
