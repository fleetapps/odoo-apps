# -*- coding: utf-8 -*-
"""Durable job queue (same architecture as our Shopify module: everything is
replayable, observable, rate-limit safe)."""
import json
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class WooSyncJob(models.Model):
    _name = "woo.bisync.job"
    _description = "Woo Sync Job"
    _order = "priority, id"

    instance_id = fields.Many2one("woo.bisync.instance", required=True,
                                  ondelete="cascade", index=True)
    direction = fields.Selection([("in", "Import"), ("out", "Export")],
                                 required=True, index=True)
    kind = fields.Selection(
        [("product", "Product"), ("stock", "Stock"), ("order", "Order"),
         ("customer", "Customer"), ("shipment", "Shipment")],
        required=True, index=True)
    payload_json = fields.Text()
    state = fields.Selection(
        [("pending", "Pending"), ("done", "Done"), ("failed", "Failed")],
        default="pending", index=True)
    priority = fields.Integer(default=10)
    error = fields.Text()
    attempt = fields.Integer(default=0)

    @api.model
    def enqueue(self, instance, direction, kind, payload, priority=10):
        return self.sudo().create({
            "instance_id": instance.id, "direction": direction, "kind": kind,
            "payload_json": json.dumps(payload, default=str),
            "priority": priority})

    @api.model
    def cron_process(self, batch=50):
        handlers = {"product": "woo.bisync.product.sync",
                    "stock": "woo.bisync.product.sync",
                    "order": "woo.bisync.order.sync",
                    "customer": "woo.bisync.order.sync",
                    "shipment": "woo.bisync.order.sync"}
        for job in self.search([("state", "=", "pending"),
                                ("attempt", "<", 4)], limit=batch):
            try:
                self.env[handlers[job.kind]].process_job(job)
                job.state = "done"
                self.env.cr.commit()
            except Exception as e:  # noqa: BLE001
                self.env.cr.rollback()
                job.write({"attempt": job.attempt + 1, "error": str(e)[:2000],
                           "state": "failed" if job.attempt >= 3 else "pending"})
                self.env.cr.commit()
                _logger.exception("Woo job %s failed", job.id)
