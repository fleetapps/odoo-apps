# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""The conflict ledger.

A two-way connector's hardest promise is not "it syncs" - it is "when both
sides changed the same record, here is exactly what happened and why". The
conflict engine in ``product_sync`` already resolves those cases and posts an
audit line on the record's chatter, but chatter is unqueryable: nobody can
answer "did Shopify overwrite anything of ours last month?" from it.

Every resolution therefore also lands here as a row, with both fingerprints
that produced the decision, the policy in force at the time and the fields
involved. That makes the audit trail filterable, groupable, exportable and -
crucially - reviewable after the fact.
"""
from odoo import fields, models


class ShopifyConflict(models.Model):
    _name = "shopify.bisync.conflict"
    _description = "Shopify Sync Conflict"
    _order = "id desc"
    _rec_name = "record_name"

    instance_id = fields.Many2one(
        "shopify.bisync.instance", required=True, ondelete="cascade",
        index=True, string="Store")
    company_id = fields.Many2one(
        related="instance_id.company_id", store=True, index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    record_name = fields.Char(
        required=True,
        help="Display name at the time of the conflict, kept as text so the "
             "log still reads correctly after the record is renamed or "
             "deleted.")
    external_id = fields.Char(index=True, help="Shopify id of the record.")
    field_names = fields.Char(
        string="Fields",
        help="Fields that actually differed between the two sides.")
    policy = fields.Selection(
        [("odoo_wins", "Odoo wins"), ("shopify_wins", "Shopify wins"),
         ("newest_wins", "Most recent edit wins")],
        required=True, help="Store policy in force when this was resolved.")
    winner = fields.Selection(
        [("odoo", "Odoo"), ("shopify", "Shopify")],
        required=True, index=True, string="Kept")
    odoo_write_date = fields.Datetime(string="Odoo Changed At")
    external_updated_at = fields.Datetime(string="Shopify Changed At")

    def action_open_record(self):
        """Jump to whatever record the conflict was about."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
            "target": "current",
        }

    @classmethod
    def log(cls, env, instance, record, binding, winner, field_names,
            external_updated_at):
        return env["shopify.bisync.conflict"].create({
            "instance_id": instance.id,
            "res_model": record._name,
            "res_id": record.id,
            "record_name": record.display_name or "?",
            "external_id": binding.external_id,
            "field_names": ", ".join(field_names) or "-",
            "policy": instance.conflict_policy,
            "winner": winner,
            "odoo_write_date": record.write_date,
            "external_updated_at": external_updated_at or False,
        })
