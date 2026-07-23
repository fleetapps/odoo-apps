# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Durable job queue: EVERYTHING crosses through here (webhook payloads in,
export intents out) so syncs are replayable, observable and rate-limit safe.

Hardening (spec A5):
- exponential backoff via ``next_attempt_at`` (2^attempt minutes);
- poison-job quarantine: after ``MAX_ATTEMPTS`` failures the job goes to the
  terminal ``failed`` state and ONE activity is scheduled for the instance's
  connector admin;
- ``lock_key``: jobs touching the same binding never interleave inside a
  batch (ir.cron itself guarantees a single runner per cron job, so a
  per-batch seen-set is sufficient - no raw SQL locking needed);
- ``Retry`` / ``Requeue all failed`` buttons;
- done/skipped jobs older than 30 days are garbage-collected by Odoo's
  autovacuum (``@api.autovacuum``).
"""
import json
import logging

from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
GC_AFTER_DAYS = 30
BACKOFF_BASE_MINUTES = 1

#: job kind -> abstract engine model implementing ``process_job(job)``.
HANDLERS = {
    "product": "shopify.bisync.product.sync",
    "price": "shopify.bisync.product.sync",
    "publish": "shopify.bisync.product.sync",
    "stock": "shopify.bisync.stock.sync",
    "order": "shopify.bisync.order.sync",
    "customer": "shopify.bisync.order.sync",
    "fulfillment": "shopify.bisync.fulfillment.sync",
    "refund": "shopify.bisync.refund.sync",
    "refund_out": "shopify.bisync.refund.sync",
    "paid": "shopify.bisync.order.export.sync",
    "cancel": "shopify.bisync.order.export.sync",
    "payout": "shopify.bisync.payout.sync",
    "backfill": "shopify.bisync.backfill.sync",
}


class SyncJob(models.Model):
    _name = "shopify.bisync.job"
    _description = "Shopify Sync Job"
    _order = "priority, id"

    instance_id = fields.Many2one(
        "shopify.bisync.instance", required=True, ondelete="cascade",
        index=True)
    company_id = fields.Many2one(
        related="instance_id.company_id", store=True, index=True)
    direction = fields.Selection(
        [("in", "Import"), ("out", "Export")], required=True, index=True)
    kind = fields.Selection(
        [("product", "Product"), ("stock", "Stock"), ("price", "Price"),
         ("publish", "Publish"), ("order", "Order"), ("customer", "Customer"),
         ("fulfillment", "Fulfillment"), ("refund", "Refund"),
         ("refund_out", "Refund → Shopify"), ("paid", "Mark Paid"),
         ("cancel", "Cancel → Shopify"), ("payout", "Payout"),
         ("backfill", "Backfill")],
        required=True, index=True)
    payload_json = fields.Text()
    state = fields.Selection(
        [("pending", "Pending"), ("done", "Done"), ("failed", "Failed"),
         ("skipped", "Skipped")], default="pending", index=True,
        help="'Failed' is the quarantine state: the job exhausted its "
             "attempts and needs a human (Retry button).")
    priority = fields.Integer(
        default=10, index=True,
        help="Lower runs first. Webhooks 10-15, stock sweep 20, price 30, "
             "backfill pages 45, backfill items 50.")
    error = fields.Text(readonly=True)
    attempt = fields.Integer(default=0, readonly=True)
    next_attempt_at = fields.Datetime(
        index=True, readonly=True,
        help="Exponential backoff: the cron skips the job until this time.")
    lock_key = fields.Char(
        index=True,
        help="Concurrency guard: jobs sharing a key are processed strictly "
             "in id order, never within the same batch.")

    # ------------------------------------------------------------------ api -
    @api.model
    def enqueue(self, instance, direction, kind, payload, priority=10,
                lock_key=None):
        """Create a job. Export intents (which are pure "re-read the record
        and push it" markers) are debounced: an identical pending job means
        the change is already scheduled."""
        payload_json = json.dumps(payload, default=str)
        if direction == "out" and lock_key:
            duplicate = self.sudo().search_count([
                ("instance_id", "=", instance.id), ("direction", "=", "out"),
                ("kind", "=", kind), ("lock_key", "=", lock_key),
                ("state", "=", "pending"), ("payload_json", "=", payload_json)],
                limit=1)
            if duplicate:
                return self.browse()
        return self.sudo().create({
            "instance_id": instance.id, "direction": direction, "kind": kind,
            "payload_json": payload_json, "priority": priority,
            "lock_key": lock_key})

    @api.model
    def cron_process(self, batch=50):
        """Process due pending jobs. Each job commits on success/failure so a
        worker kill loses at most the in-flight job (which stays pending)."""
        now = fields.Datetime.now()
        jobs = self.search([
            ("state", "=", "pending"),
            "|", ("next_attempt_at", "=", False),
            ("next_attempt_at", "<=", now)], limit=batch)
        seen_locks = set()
        for job in jobs:
            if job.lock_key:
                if job.lock_key in seen_locks:
                    continue  # same binding already touched in this batch
                seen_locks.add(job.lock_key)
            job._run_one()

    def _run_one(self):
        self.ensure_one()
        try:
            self.env[HANDLERS[self.kind]].process_job(self)
            self.write({"state": "done", "error": False})
            self.env.cr.commit()  # keep progress; each job is atomic
        except Exception as exc:  # noqa: BLE001 - queue must survive anything
            self.env.cr.rollback()
            attempt = self.attempt + 1
            vals = {"attempt": attempt, "error": str(exc)[:2000]}
            if attempt >= MAX_ATTEMPTS:
                vals["state"] = "failed"  # quarantine
            else:
                vals["next_attempt_at"] = fields.Datetime.now() + timedelta(
                    minutes=BACKOFF_BASE_MINUTES * 2 ** attempt)
            self.write(vals)
            if attempt >= MAX_ATTEMPTS:
                self._notify_quarantine()
            self.env.cr.commit()
            _logger.exception("shopify_bisync: job %s failed (attempt %s)",
                              self.id, attempt)

    def _notify_quarantine(self):
        """One activity to the connector admin per quarantined job."""
        instance = self.instance_id
        instance.activity_schedule(
            "mail.mail_activity_data_todo",
            user_id=(instance.admin_user_id or self.env.user).id,
            summary=_("Shopify sync job #%s quarantined", self.id),
            note=_("Job %(kind)s/%(direction)s failed %(n)s times. "
                   "Last error: %(error)s",
                   kind=self.kind, direction=self.direction,
                   n=self.attempt, error=(self.error or "")[:500]))

    # -------------------------------------------------------------- buttons -
    def action_retry(self):
        """Put the job back in front of the queue, bypassing backoff."""
        self.write({"state": "pending", "next_attempt_at": False,
                    "attempt": 0, "error": False})

    @api.model
    def requeue_all_failed(self):
        """Server-action target: requeue every quarantined job."""
        self.search([("state", "=", "failed")]).action_retry()

    # --------------------------------------------------------------- vacuum -
    @api.autovacuum
    def _gc_finished_jobs(self):
        """Drop done/skipped jobs older than 30 days (runs with Odoo's daily
        autovacuum cron)."""
        cutoff = fields.Datetime.now() - timedelta(days=GC_AFTER_DAYS)
        jobs = self.search([("state", "in", ("done", "skipped")),
                            ("write_date", "<", cutoff)], limit=10000)
        jobs.unlink()
        return True
