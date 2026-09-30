# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""A5/A7: queue hardening - exponential backoff, poison-job quarantine with
admin activity, lock_key batch isolation, enqueue debounce, autovacuum."""
from datetime import timedelta

from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .common import ShopifyBisyncCase


@tagged("post_install", "-at_install")
class TestJobQueue(ShopifyBisyncCase):

    def _patch_handler(self, side_effect):
        patcher = patch.object(
            self.env.registry["shopify.bisync.product.sync"], "process_job",
            side_effect=side_effect)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_01_backoff_sets_next_attempt(self):
        self._patch_handler(Exception("boom"))
        job = self.Job.enqueue(self.instance, "out", "product",
                               {"res_id": 1}, lock_key="product:1")
        self.run_jobs()
        self.assertEqual((job.state, job.attempt), ("pending", 1))
        self.assertTrue(job.next_attempt_at > fields.Datetime.now(),
                        "backoff must push the retry into the future")
        # Not due yet -> the cron must not touch it again.
        self.run_jobs()
        self.assertEqual(job.attempt, 1)

    def test_02_quarantine_after_max_attempts_with_activity(self):
        self._patch_handler(Exception("still broken"))
        job = self.Job.enqueue(self.instance, "out", "product",
                               {"res_id": 2}, lock_key="product:2")
        for __ in range(4):
            job.next_attempt_at = False  # force due
            self.run_jobs()
        self.assertEqual((job.state, job.attempt), ("failed", 4))
        activity = self.instance.activity_ids.filtered(
            lambda a: "quarantined" in (a.summary or ""))
        self.assertTrue(activity, "one activity to the connector admin")
        self.assertEqual(activity.user_id, self.instance.admin_user_id)
        # Retry button resets and bypasses backoff.
        job.action_retry()
        self.assertEqual((job.state, job.attempt), ("pending", 0))
        self.assertFalse(job.next_attempt_at)

    def test_03_lock_key_no_interleave_within_batch(self):
        processed = []
        self._patch_handler(lambda job: processed.append(job.id))
        first = self.Job.enqueue(self.instance, "in", "product",
                                 {"id": 1, "seq": 1}, lock_key="product:X")
        second = self.Job.enqueue(self.instance, "in", "product",
                                  {"id": 1, "seq": 2}, lock_key="product:X")
        self.run_jobs()
        self.assertEqual(processed, [first.id],
                         "second job with same lock_key deferred")
        self.assertEqual((first.state, second.state), ("done", "pending"))
        self.run_jobs()
        self.assertEqual(second.state, "done")

    def test_04_enqueue_debounce(self):
        job = self.Job.enqueue(self.instance, "out", "stock",
                               {"res_id": 9}, lock_key="stock:9")
        duplicate = self.Job.enqueue(self.instance, "out", "stock",
                                     {"res_id": 9}, lock_key="stock:9")
        self.assertTrue(job)
        self.assertFalse(duplicate, "identical pending export debounced")

    def test_05_autovacuum_gc(self):
        job = self.Job.enqueue(self.instance, "out", "product",
                               {"res_id": 3}, lock_key="product:3")
        job.state = "done"
        old = fields.Datetime.now() - timedelta(days=40)
        # Backdating write_date needs SQL (ORM protects log fields);
        # test-only usage.
        self.env.cr.execute(
            "UPDATE shopify_bisync_job SET write_date = %s WHERE id = %s",
            (old, job.id))
        self.Job.invalidate_model(["write_date"])
        self.Job._gc_finished_jobs()
        self.assertFalse(job.exists(), "done jobs older than 30 days vacuumed")

    def test_06_priority_order(self):
        processed = []
        self._patch_handler(lambda job: processed.append(job.id))
        backfill_item = self.Job.enqueue(self.instance, "in", "product",
                                         {"id": 5}, priority=50)
        webhook = self.Job.enqueue(self.instance, "in", "product",
                                   {"id": 6}, priority=10)
        self.run_jobs()
        self.assertEqual(processed, [webhook.id, backfill_item.id],
                         "live webhooks outrank backfill")
