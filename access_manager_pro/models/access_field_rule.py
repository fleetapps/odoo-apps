# -*- coding: utf-8 -*-
"""Field-level access rules (``access.manager.field.rule``).

One line per (profile, model, field).  A field can be made invisible,
read-only or required, and relational fields can additionally have their inline
*Create* / *Create and Edit* option and their *Internal / External link* button
removed.  An optional ``condition`` turns any of these into a conditional
attribute evaluated against the record in the client.
"""

from odoo import api, fields, models


class AccessFieldRule(models.Model):
    _name = "access.manager.field.rule"
    _inherit = "access.manager.cache.mixin"
    _description = "Access Manager - Field Rule"
    _rec_name = "field_id"

    profile_id = fields.Many2one(
        "access.manager.profile", required=True, ondelete="cascade", index=True)
    model_id = fields.Many2one(
        "ir.model", string="Model", required=True, ondelete="cascade",
        domain="[('transient', '=', False)]",
        help="The model the field belongs to, e.g. Sales Order. Pick it first "
             "- it narrows the field list below.")
    model_name = fields.Char(
        related="model_id.model", store=True, index=True, string="Model Name")
    field_id = fields.Many2one(
        "ir.model.fields", string="Field", required=True, ondelete="cascade",
        domain="[('model_id', '=', model_id)]",
        help="The field to restrict. The rule follows this field into every "
             "view of the model - form, list, kanban and search alike.")
    field_name = fields.Char(
        related="field_id.name", store=True, string="Field Name")

    mode = fields.Selection(
        [("invisible", "Invisible"),
         ("readonly", "Read-Only"),
         ("required", "Required"),
         ("masked", "Masked (Sensitive Data)")],
        required=True, default="invisible",
        help="Invisible also removes the field from list columns, search "
             "filters, group-by, kanban and server-side export. Masked replaces "
             "the value with a masked string on the client and makes the field "
             "read-only.")
    mask_char = fields.Char(
        string="Mask Character", default="•", size=1,
        help="Character repeated to mask the value.")
    mask_show_last = fields.Integer(
        string="Reveal Last N", default=0,
        help="Number of trailing characters left visible (e.g. 4 -> "
             "'••••••1234'). 0 masks the whole value.")
    no_create = fields.Boolean(
        string="No Create / Create & Edit",
        help="On relational fields, remove the inline 'Create' and "
             "'Create and Edit' quick-create options from the dropdown.")
    no_open = fields.Boolean(
        string="No Internal / External Link",
        help="On relational fields, remove the button that opens the linked "
             "record.")
    condition = fields.Char(
        string="Condition",
        help="Optional Python attribute expression evaluated on the record, "
             "e.g. state == 'done'. When set, the chosen mode is applied only "
             "when the expression is truthy. Leave empty to always apply.")

    _field_mode_uniq = models.Constraint(
        "UNIQUE (profile_id, field_id, mode)",
        "This field already has a rule with the same mode in this profile.",
    )

    @api.onchange("model_id")
    def _onchange_model_id(self):
        self.field_id = False
