# -*- coding: utf-8 -*-
"""Governance scope: the heart of the "enterprises can approve it" pitch.

A scope answers two orthogonal questions for a connection:

1. *Which MCP tools are even visible?*  -> capability_ids (Sales, Inventory, ...)
2. *Which data may those tools touch?*  -> line_ids (per-model r/c/w/u + field
   blacklist + extra record domain)

Both are ANDed with the acting user's native Odoo permissions, so a scope can
only ever *narrow* access, never widen it. This is defence-in-depth: even a
misconfigured scope cannot hand an AI more than the underlying user already has.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Permission presets, as label -> (read, create, write, unlink). They live here
# rather than on the wizard because the install hook and the Connect screen add
# models too, and three copies of this tuple would drift.
PRESETS = {
    "read": (True, False, False, False),
    "draft": (True, True, True, False),
    "full": (True, True, True, True),
}

# The models a business actually asks questions about. Referencing any of these
# from a data file would break installation on a database without that app, so
# they are resolved at run time instead - ir.model._get returns an empty
# recordset for a model that is not installed, and the absent ones are skipped.
# Ordered deliberately: sales first, because that is what the first question is
# almost always about.
SUGGESTED_MODELS = (
    "sale.order",
    "sale.order.line",
    "purchase.order",
    "purchase.order.line",
    "account.move",
    "account.move.line",
    "product.template",
    "product.product",
    "stock.quant",
    "stock.picking",
    "crm.lead",
    "project.project",
    "project.task",
    "hr.employee",
)

# Never callable over MCP, whatever an admin types into the allow-list. These
# are either raw ORM verbs (already covered by the governed read/write tools,
# which audit and approval-gate) or privilege-escalation paths that would let a
# method call step outside the acting user's rights.
DENIED_METHODS = {
    # privilege escalation
    "sudo", "with_user", "with_env", "with_context", "with_company",
    # raw ORM data access - use the governed tools, which are audited
    "create", "write", "unlink", "copy", "browse", "search", "search_read",
    "search_count", "read", "read_group", "load", "export_data",
    "name_create", "default_get", "new", "update", "fields_get",
    # access-control internals
    "check_access", "check_access_rights", "check_access_rule",
    "invalidate_cache", "invalidate_model", "flush", "flush_model", "modified",
    # remote execution surface
    "execute_kw", "execute", "run", "eval",
}


class MCPScope(models.Model):
    _name = "mcp.scope"
    _description = "MCP Governance Scope"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    description = fields.Text(
        translate=True,
        help="Shown to admins; not exposed to the AI client.")
    read_only = fields.Boolean(
        default=True,
        help="Global kill-switch. When on, create/write/unlink tools are not "
             "even advertised in tools/list - the safest default.")
    require_approval = fields.Boolean(
        default=True,
        help="Write operations create a human approval request instead of "
             "executing immediately. Ignored when Read Only is on.")
    rate_limit_per_hour = fields.Integer(
        default=500,
        help="Maximum tool calls per rolling hour for any connection using this "
             "scope. 0 disables the limit.")
    max_records = fields.Integer(
        default=200,
        help="Hard cap on rows returned by a single search, protecting the "
             "database from a run-away AI query on a 100k+ record model.")
    capability_ids = fields.Many2many(
        "mcp.capability", string="Capabilities",
        help="Which capability bundles (and therefore which tools) this scope "
             "exposes. Empty means every installed capability.")
    line_ids = fields.One2many("mcp.scope.line", "scope_id", copy=True)
    line_count = fields.Integer(compute="_compute_line_count")
    key_count = fields.Integer(compute="_compute_key_count")

    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    def _compute_key_count(self):
        data = self.env["mcp.api.key"].sudo()._read_group(
            [("scope_id", "in", self.ids)], groupby=["scope_id"], aggregates=["__count"])
        counts = {s.id: n for s, n in data}
        for rec in self:
            rec.key_count = counts.get(rec.id, 0)

    def allowed_capabilities(self):
        """Effective capabilities: explicit selection, else all installed."""
        self.ensure_one()
        if self.capability_ids:
            return self.capability_ids.filtered("active")
        return self.env["mcp.capability"].search([("active", "=", True)])

    def line_for_model(self, model_name):
        """The matrix row governing this model, or an empty recordset.

        Filtered on `active` explicitly rather than relying on the one2many to
        do it: a caller running with ``active_test=False`` in context would
        otherwise resolve an archived row and be granted access an
        administrator believed they had suspended.
        """
        self.ensure_one()
        return self.line_ids.filtered(
            lambda l: l.active and l.model_name == model_name)[:1]

    # ------------------------------------------------------------ bulk adding
    def existing_model_ids(self):
        """Model ids already on this scope, *including archived rows*.

        The uniqueness constraint is enforced in the database, which does not
        know about archiving. Reading ``line_ids`` would silently hide an
        archived row and the insert would then fail on that constraint.
        """
        self.ensure_one()
        return set(self.env["mcp.scope.line"].with_context(active_test=False)
                   .search([("scope_id", "=", self.id)]).mapped("model_id").ids)

    def add_models(self, model_names, preset="read"):
        """Add matrix rows for `model_names`, skipping what is already there.

        Safe to re-run and safe to hand a model that is not installed: an
        absent model resolves to an empty recordset and is skipped. Returns the
        rows actually created, so a caller can report a count honestly.
        """
        self.ensure_one()
        can_read, can_create, can_write, can_unlink = PRESETS[preset]
        seen = self.existing_model_ids()
        values = []
        for name in model_names:
            model = self.env["ir.model"]._get(name)
            # `seen` also absorbs duplicates inside model_names itself, which
            # would otherwise hit the constraint on the second row.
            if not model or model.id in seen:
                continue
            seen.add(model.id)
            values.append({
                "scope_id": self.id,
                "model_id": model.id,
                "can_read": can_read,
                "can_create": can_create,
                "can_write": can_write,
                "can_unlink": can_unlink,
            })
        Line = self.env["mcp.scope.line"]
        return Line.create(values) if values else Line.browse()

    def readable_model_names(self):
        """Models this scope can currently read, deterministically ordered.

        Sorted because MCP asks for a deterministic tool list, and these names
        are spelled out in the read tools' descriptions.
        """
        self.ensure_one()
        return sorted(set(self.line_ids
                          .filtered(lambda l: l.active and l.can_read)
                          .mapped("model_name")))


class MCPScopeLine(models.Model):
    """One model's permissions inside a scope - the row of the access matrix.

    Enforcement is always ``min(this row, the acting user's Odoo rights)``. A
    row can only ever *narrow* what the bound user could already do; it can
    never widen it. That is why this is safe to expose as a flat, quickly
    editable matrix: the worst a misconfiguration can do is grant something the
    user already had.
    """
    _name = "mcp.scope.line"
    _description = "MCP Model Permission"
    _order = "scope_id, model_name"
    _rec_name = "model_name"

    scope_id = fields.Many2one(
        "mcp.scope", required=True, ondelete="cascade", index=True)
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    model_name = fields.Char(related="model_id.model", store=True, index=True)
    # Not stored: ir.model.name is translatable, and a stored related copy of a
    # translated field goes stale per-language. Display only.
    model_label = fields.Char(
        related="model_id.name", string="Model", readonly=True)
    active = fields.Boolean(
        default=True,
        help="Archive a row to suspend all AI access to this model without "
             "losing how it was configured.")
    can_read = fields.Boolean(string="Read", default=True)
    can_create = fields.Boolean(string="Create", default=False)
    can_write = fields.Boolean(string="Update", default=False)
    can_unlink = fields.Boolean(string="Delete", default=False)
    can_call_methods = fields.Boolean(
        string="Method Calls", default=False,
        help="DANGEROUS. Lets the AI invoke business methods on this model "
             "(confirm, post, send...). Nothing is callable until you also "
             "list the exact method names below - an empty list allows "
             "nothing. Never enable this to 'see what happens'.")
    allowed_methods = fields.Char(
        string="Allowed Methods",
        help="Comma-separated allow-list, e.g. action_confirm,action_post. "
             "Only these exact names can be called. Private methods (leading "
             "underscore) and raw ORM verbs are always refused.")
    field_blacklist = fields.Char(
        help="Comma-separated fields never returned or accepted, e.g. "
             "password,vat,bank_ids. Applied on top of Odoo field ACLs.")
    record_domain = fields.Char(
        default="[]",
        help="Extra Odoo domain ANDed to every read/search on this model, e.g. "
             "[('state','!=','draft')].")
    scope_read_only = fields.Boolean(
        related="scope_id.read_only", string="Scope is Read Only", readonly=True)
    write_bits_inert = fields.Boolean(
        compute="_compute_write_bits_inert",
        string="Write switches have no effect",
        help="The owning scope's Read Only kill switch overrides this row, so "
             "the Create/Update/Delete/Method switches below do nothing.")

    @api.depends("scope_read_only", "can_create", "can_write", "can_unlink",
                 "can_call_methods")
    def _compute_write_bits_inert(self):
        """Flag rows whose write switches are silently overridden.

        Switching on Create here while the scope has Read Only on is the single
        easiest way to misconfigure this module: the toggles save, look right,
        and change nothing. The row has to say so.
        """
        for rec in self:
            rec.write_bits_inert = rec.scope_read_only and any((
                rec.can_create, rec.can_write, rec.can_unlink,
                rec.can_call_methods))

    _model_uniq = models.Constraint(
        "UNIQUE (scope_id, model_id)",
        "Each model can appear only once per scope.")

    @api.constrains("can_call_methods", "allowed_methods")
    def _check_allowed_methods(self):
        """Reject method names that could never be safely called.

        Catching this at save time means an admin finds out here, in context,
        rather than through a puzzling refusal at run time.
        """
        for rec in self:
            if not rec.can_call_methods:
                continue
            for method in rec.allowed_method_set():
                if method.startswith("_"):
                    raise ValidationError(_(
                        "'%s' is a private method and can never be exposed.")
                        % method)
                if method in DENIED_METHODS:
                    raise ValidationError(_(
                        "'%(method)s' is a raw ORM or privilege method and is "
                        "never callable over MCP. Use the governed read/write "
                        "tools instead.", method=method))
                if not hasattr(self.env[rec.model_name], method):
                    raise ValidationError(_(
                        "Model %(model)s has no method '%(method)s'.",
                        model=rec.model_name, method=method))

    def blacklisted_fields(self):
        self.ensure_one()
        return {f for f in (self.field_blacklist or "").replace(" ", "").split(",") if f}

    def allowed_method_set(self):
        self.ensure_one()
        return {m for m in (self.allowed_methods or "").replace(" ", "").split(",") if m}

    def action_open_scope(self):
        """Jump from a matrix row to the scope that owns it."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "mcp.scope",
            "res_id": self.scope_id.id,
            "view_mode": "form",
        }
