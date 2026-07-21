# -*- coding: utf-8 -*-
"""Durable job queue: EVERYTHING crosses through here (webhook payloads in,
export intents out) so syncs are replayable, observable and rate-limit safe."""
import json
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SyncJob(models.Model):
    _name = "shopify.bisync.job"
    _description = "Sync Job"
    _order = "priority, id"

    instance_id = fields.Many2one("shopify.bisync.instance", required=True,
                                  ondelete="cascade", index=True)
    direction = fields.Selection([("in", "Import"), ("out", "Export")],
                                 required=True, index=True)
    kind = fields.Selection(
        [("product", "Product"), ("stock", "Stock"), ("price", "Price"),
         ("order", "Order"), ("customer", "Customer"),
         ("fulfillment", "Fulfillment"), ("refund", "Refund")],
        required=True, index=True)
    payload_json = fields.Text()
    state = fields.Selection(
        [("pending", "Pending"), ("done", "Done"), ("failed", "Failed"),
         ("skipped", "Skipped")], default="pending", index=True)
    priority = fields.Integer(default=10)
    error = fields.Text()
    attempt = fields.Integer(default=0)

    @api.model
    def enqueue(self, instance, direction, kind, payload, priority=10):
        return self.sudo().create({
            "instance_id": instance.id, "direction": direction, "kind": kind,
            "payload_json": json.dumps(payload, default=str), "priority": priority})

    @api.model
    def cron_process(self, batch=50):
        for job in self.search([("state", "=", "pending"),
                                ("attempt", "<", 4)], limit=batch):
            handler_model = {
                "product": "shopify.bisync.product.sync",
                "stock": "shopify.bisync.stock.sync",
                "price": "shopify.bisync.stock.sync",
                "order": "shopify.bisync.order.sync",
                "customer": "shopify.bisync.order.sync",
                "fulfillment": "shopify.bisync.order.sync",
                "refund": "shopify.bisync.order.sync",
            }[job.kind]
            try:
                self.env[handler_model].process_job(job)
                job.state = "done"
                self.env.cr.commit()  # keep progress; each job is atomic
            except Exception as e:  # noqa: BLE001
                self.env.cr.rollback()
                job.write({"attempt": job.attempt + 1, "error": str(e)[:2000],
                           "state": "failed" if job.attempt >= 3 else "pending"})
                self.env.cr.commit()
                _logger.exception("Sync job %s failed", job.id)
