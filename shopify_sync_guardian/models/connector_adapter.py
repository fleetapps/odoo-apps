# -*- coding: utf-8 -*-
"""Connector adapters - runtime detection of whichever Shopify connector is
installed, so this app ships with zero hard dependencies.

ORM env/registry access per:
https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html

VERIFY-ON-BUILD: the queue/log model names below are the connectors' internal
models and can change between connector versions. Confirm against the installed
connector's models before release (Settings > Technical > Models). Adapters
fail soft: if a model is missing, that capability is disabled, not broken.
"""
import logging
from odoo import api, models

_logger = logging.getLogger(__name__)


class GuardianAdapter(models.AbstractModel):
    _name = "guardian.adapter"
    _description = "Shopify Connector Adapter"

    # capability map per vendor: model names to probe at runtime
    VENDOR_PROBES = {
        "emipro": {
            "order_queue": "shopify.order.data.queue.ept",
            "order_queue_line": "shopify.order.data.queue.line.ept",
            "product_queue": "shopify.product.data.queue.ept",
            "instance": "shopify.instance.ept",
            "log": "common.log.book.ept",
        },
        "ventortech": {
            "instance": "sale.integration",
            "job": "queue.job",          # VentorTech uses OCA queue_job
        },
        "teqstars": {
            "instance": "shopify.instance",
            "job": "shopify.queue.job",
        },
    }

    @api.model
    def detect_vendor(self):
        """Return (vendor_key, probes) for the first connector found."""
        for vendor, probes in self.VENDOR_PROBES.items():
            if probes["instance"] in self.env:
                return vendor, probes
        return None, {}

    @api.model
    def scan_failed(self):
        """Yield dicts describing failed/stuck sync units from the detected
        connector. Each dict: {res_model, res_id, kind, name, error}."""
        vendor, probes = self.detect_vendor()
        if not vendor:
            return []
        found = []
        if vendor == "emipro":
            Line = self.env[probes["order_queue_line"]].sudo()
            # Emipro queue lines carry a state field; 'failed' is the error state.
            for line in Line.search([("state", "=", "failed")], limit=500):
                found.append({
                    "res_model": Line._name, "res_id": line.id,
                    "kind": "failed_order",
                    "name": line.display_name,
                    "error": getattr(line, "common_log_lines_ept", False) and
                             ", ".join(line.mapped("common_log_lines_ept.message")[:3]) or "",
                })
        elif vendor in ("ventortech", "teqstars"):
            Job = self.env[probes["job"]].sudo()
            state_field = "state"
            for job in Job.search([(state_field, "in", ("failed", "error"))], limit=500):
                found.append({
                    "res_model": Job._name, "res_id": job.id,
                    "kind": "failed_job", "name": job.display_name,
                    "error": getattr(job, "exc_info", "") or getattr(job, "error", "") or "",
                })
        return found

    @api.model
    def retry(self, issue):
        """Best-effort re-run of the underlying queue record."""
        rec = self.env[issue.res_model].sudo().browse(issue.res_id).exists()
        if not rec:
            return False
        for method in ("process_queue_line", "requeue", "action_requeue",
                       "run", "process"):
            if hasattr(rec, method):
                try:
                    getattr(rec, method)()
                    return True
                except Exception as e:   # noqa: BLE001 - must never crash the cron
                    _logger.warning("Guardian retry failed on %s#%s: %s",
                                    issue.res_model, issue.res_id, e)
                    return False
        return False

    @api.model
    def reconcile_orphans(self):
        """Orders present in Shopify but missing in Odoo (and vice versa).
        Implementation: compare connector instance's remote order refs vs
        sale.order client_order_ref / origin. Scaffold returns []; wire per
        vendor at build time."""
        return []
