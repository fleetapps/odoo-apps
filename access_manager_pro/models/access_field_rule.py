# -*- coding: utf-8 -*-
"""Field-level access rules (``access.manager.field.rule``).

One line per (profile, model, field).  A field can be made invisible,
read-only or required, and relational fields can additionally have their inline
*Create* / *Create and Edit* option and their *Internal / External link* button
removed.  An optional ``condition`` turns any of these into a conditional
attribute evaluated against the record in the client.
"""

import ast

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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
    dropdown_domain = fields.Char(
        string="Apply Field Domain",
        help="On a relational field, narrow the records offered in its "
             "dropdown, e.g. [('country_id.code', '=', 'KE')]. It is combined "
             "with any domain the view already sets, never replacing it. The "
             "same dynamic names a view domain may use are available "
             "(uid, context, and the other fields of the record).\n"
             "This filters the picker; to make the records genuinely "
             "unreachable, add a Record (Domain) Rule on the target model.")

    _field_mode_uniq = models.Constraint(
        "UNIQUE (profile_id, field_id, mode)",
        "This field already has a rule with the same mode in this profile.",
    )

    @api.constrains("dropdown_domain")
    def _check_dropdown_domain(self):
        """Reject anything that is not a list-shaped Python expression.

        A view domain is evaluated in the browser against the record, so it may
        legitimately reference field names and helpers that mean nothing here -
        ``ast.literal_eval`` would reject those. Parse it as an expression
        instead and only insist that it *looks* like a domain, which is what
        the arch merge below needs to stay valid.
        """
        for rule in self:
            raw = (rule.dropdown_domain or "").strip()
            if not raw:
                continue
            try:
                tree = ast.parse(raw, mode="eval")
            except SyntaxError as exc:
                raise ValidationError(_(
                    "The field domain of %(field)s is not a valid Python "
                    "expression: %(error)s", field=rule.field_name or "?",
                    error=exc)) from exc
            if not isinstance(tree.body, (ast.List, ast.Tuple, ast.BinOp,
                                          ast.Call, ast.IfExp, ast.Name)):
                raise ValidationError(_(
                    "The field domain of %(field)s must evaluate to a list of "
                    "conditions, e.g. [('active', '=', True)].",
                    field=rule.field_name or "?"))

    @api.onchange("model_id")
    def _onchange_model_id(self):
        self.field_id = False
