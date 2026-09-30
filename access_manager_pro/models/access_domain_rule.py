# -*- coding: utf-8 -*-
"""Record-level (conditional) access rules (``access.manager.domain.rule``).

A domain rule describes a set of records - via an Odoo domain - that the
targeted users are restricted from.  The restriction can apply to any
combination of read, create, write and unlink.

* ``read``  hides the matching records from lists, searches and relational
  widgets (they are filtered out of every search on the model).
* ``create`` / ``write`` / ``unlink`` raise an :class:`AccessError` when the
  user tries to create, edit or delete a matching record.

A *soft* rule only ever hides records (read filtering); it never raises, which
is useful when the goal is to declutter rather than to forbid.

The domain supports the same dynamic helpers as native record rules
(``user``, ``company_ids``, ``time``, ``datetime``, ``relativedelta`` ...), so
date windows such as ``[('date', '<', (context_today() -
relativedelta(days=30)))]`` work out of the box.
"""

import ast

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AccessDomainRule(models.Model):
    _name = "access.manager.domain.rule"
    _inherit = "access.manager.cache.mixin"
    _description = "Access Manager - Record (Domain) Rule"

    name = fields.Char(
        required=True, default="Restriction",
        help="Shown to the user in the error message when the rule blocks "
             "them, so word it as the reason, e.g. 'Only your own orders'.")
    profile_id = fields.Many2one(
        "access.manager.profile", required=True, ondelete="cascade", index=True)
    model_id = fields.Many2one(
        "ir.model", string="Model", required=True, ondelete="cascade",
        domain="[('transient', '=', False)]",
        help="The model whose records are restricted, e.g. Sales Order.")
    model_name = fields.Char(
        related="model_id.model", store=True, index=True, string="Model Name")

    domain = fields.Char(
        required=True, default="[]",
        help="Records matching this domain are restricted for the targeted "
             "users. Supports the dynamic helpers available to record rules. "
             "Ignored when 'Hierarchy access' is enabled.")

    # --- Hierarchy access ----------------------------------------------------
    use_hierarchy = fields.Boolean(
        string="Hierarchy Access",
        help="Restrict to records whose user field points to the current user "
             "(and, optionally, their subordinates in the employee hierarchy). "
             "Replaces the domain above.")
    hierarchy_field_id = fields.Many2one(
        "ir.model.fields", string="User Field", ondelete="cascade",
        domain="[('model_id', '=', model_id), ('relation', '=', 'res.users')]",
        help="A Many2one-to-Users field on the model (e.g. Salesperson).")
    hierarchy_mode = fields.Selection(
        [("own", "Own records only"),
         ("subordinates", "Own + subordinates (employee hierarchy)")],
        default="subordinates", string="Scope",
        help="Own records only: strictly their own. Own + subordinates: also "
             "the records of everyone reporting to them, read from the "
             "manager chain in Employees.")
    hierarchy_field_name = fields.Char(
        related="hierarchy_field_id.name", store=True)
    perm_read = fields.Boolean(
        string="Restrict Read", default=True,
        help="Hide the matching records from the targeted users.")
    perm_create = fields.Boolean(
        string="Restrict Create",
        help="Refuse creating a record that would match this rule, e.g. an "
             "order assigned to somebody else.")
    perm_write = fields.Boolean(
        string="Restrict Edit",
        help="Refuse editing a matching record. Combine with Restrict Read to "
             "hide it outright, or leave Read off to show it read-only.")
    perm_unlink = fields.Boolean(
        string="Restrict Delete", help="Refuse deleting a matching record.")
    is_soft = fields.Boolean(
        string="Soft Restriction",
        help="Only hide the matching records; never raise an error on "
             "create/edit/delete.")

    @api.constrains("domain")
    def _check_domain(self):
        """Fail fast on a syntactically invalid domain literal."""
        for rule in self:
            try:
                parsed = ast.literal_eval(rule.domain or "[]")
            except (ValueError, SyntaxError) as exc:
                raise ValidationError(
                    _("The domain of %(name)s is not a valid Python literal: "
                      "%(error)s", name=rule.name, error=exc))
            if not isinstance(parsed, (list, tuple)):
                raise ValidationError(
                    _("The domain of %(name)s must be a list of tuples.",
                      name=rule.name))

    @api.constrains("perm_read", "perm_create", "perm_write", "perm_unlink")
    def _check_any_perm(self):
        for rule in self:
            if not any((rule.perm_read, rule.perm_create,
                        rule.perm_write, rule.perm_unlink)):
                raise ValidationError(
                    _("Domain rule %(name)s must restrict at least one "
                      "operation.", name=rule.name))

    @api.constrains("use_hierarchy", "hierarchy_field_id")
    def _check_hierarchy(self):
        for rule in self:
            if rule.use_hierarchy and not rule.hierarchy_field_id:
                raise ValidationError(
                    _("Hierarchy access on %(name)s needs a user field.",
                      name=rule.name))

    @api.onchange("model_id")
    def _onchange_model_id(self):
        self.hierarchy_field_id = False
