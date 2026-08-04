# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Binding pattern: one row per (odoo record <-> external id <-> instance).

Modeled after the well-established OCA connector binding architecture so data
stays clean with multiple stores. Every synced entity gets a binding row with
sync fingerprints for conflict detection:

- ``external_updated_at``: Shopify's ``updatedAt`` at last successful sync;
- ``odoo_write_date``: the record's ``write_date`` at last successful sync;
- ``checksum``: hash of the full export payload INCLUDING the image hash, so
  an unchanged record produces zero API calls;
- ``inventory_item_id``: for variant bindings, the Shopify InventoryItem
  numeric id needed by ``inventorySetQuantities``.

``external_id`` always stores the NUMERIC Shopify id (webhooks speak REST
ids); GraphQL GIDs are derived via ``instance.gid()``.
"""
from odoo import fields, models


class ShopifyBinding(models.Model):
    _name = "shopify.bisync.binding"
    _description = "Shopify Binding"
    _rec_name = "external_id"

    _uniq_binding = models.Constraint(
        "UNIQUE(instance_id, res_model, res_id)",
        "Record already bound to this store.")
    _uniq_external = models.Constraint(
        "UNIQUE(instance_id, res_model, external_id)",
        "External id already bound.")

    instance_id = fields.Many2one(
        "shopify.bisync.instance", required=True, ondelete="cascade",
        index=True)
    company_id = fields.Many2one(
        related="instance_id.company_id", store=True, index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    external_id = fields.Char(required=True, index=True)
    inventory_item_id = fields.Char(
        index=True,
        help="Shopify InventoryItem id (variant bindings only).")
    shopify_status = fields.Char(
        help="Last known Shopify product status (active/draft/archived) so a "
             "round-trip does not silently promote drafts.")
    external_updated_at = fields.Datetime(
        help="Shopify's updated_at at last sync - conflict detection input.")
    odoo_write_date = fields.Datetime(
        help="Odoo write_date at last sync - conflict detection input.")
    checksum = fields.Char(
        help="Hash of the last exported payload (all fields + image hash); "
             "identical hash -> export skipped entirely.")
    image_checksum = fields.Char(
        help="Hash of the Odoo-side gallery, to skip media re-uploads.")
    image_source_hash = fields.Char(
        help="Hash of the Shopify image URLs at last import. Shopify's URLs "
             "carry a version, so an unchanged hash means an unchanged "
             "gallery and the images are not downloaded again.")
    image_map_json = fields.Text(
        help="{odoo image sha1: Shopify file GID}. productSet reconciles the "
             "media list declaratively, so every export has to send the WHOLE "
             "gallery; this map lets the ones Shopify already holds go as an "
             "id reference instead of a fresh upload.")

    def resolve(self):
        self.ensure_one()
        record = self.env[self.res_model].browse(self.res_id)
        return record if record.exists() else self.env[self.res_model]

    # ------------------------------------------------------------- helpers --
    @classmethod
    def _domain(cls, instance, res_model, external_id=None, record=None):
        domain = [("instance_id", "=", instance.id),
                  ("res_model", "=", res_model)]
        if external_id is not None:
            domain.append(("external_id", "=", str(external_id)))
        if record is not None:
            domain.append(("res_id", "=", record.id))
        return domain

    @classmethod
    def get(cls, env, instance, res_model, external_id=None, record=None):
        """Find one binding either side of the pair (by external id and/or
        Odoo record)."""
        return env["shopify.bisync.binding"].search(
            cls._domain(instance, res_model, external_id, record), limit=1)
