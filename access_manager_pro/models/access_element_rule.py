# -*- coding: utf-8 -*-
"""View-element access rules (``access.manager.element.rule``).

One line per (profile, model, element).  It hides or disables arbitrary view
elements addressed by a simple selector: header buttons, statusbar buttons,
notebook tabs, kanban links, search filters, group-by options, or - as an
escape hatch - a raw XPath.  An optional ``condition`` makes the element hidden
only when the expression is truthy on the record.
"""

from odoo import api, fields, models


class AccessElementRule(models.Model):
    _name = "access.manager.element.rule"
    _inherit = "access.manager.cache.mixin"
    _description = "Access Manager - View Element Rule"
    _rec_name = "selector"

    profile_id = fields.Many2one(
        "access.manager.profile", required=True, ondelete="cascade", index=True)
    model_id = fields.Many2one(
        "ir.model", string="Model", required=True, ondelete="cascade",
        domain="[('transient', '=', False)]",
        help="The model whose views contain the element, e.g. Sales Order.")
    model_name = fields.Char(
        related="model_id.model", store=True, index=True, string="Model Name")

    element_type = fields.Selection(
        [("button", "Button (by name)"),
         ("page", "Notebook Tab (by label or name)"),
         ("filter", "Search Filter (by name)"),
         ("groupby", "Group By option (by name)"),
         ("kanban", "Kanban Link / Element (by class or name)"),
         ("xpath", "Custom XPath")],
        required=True, default="button",
        help="What kind of thing to target. This decides how the selector "
             "below is interpreted, so pick the type first.")
    selector = fields.Char(
        required=True,
        help="button: the button 'name' attribute (method or action id)\n"
             "page: the tab label (string) or its 'name' attribute\n"
             "filter / groupby: the filter 'name' attribute\n"
             "kanban: a CSS class or 'name' present on the element\n"
             "xpath: any XPath, e.g. //group[@name='pricing']")
    mode = fields.Selection(
        [("invisible", "Hide"), ("readonly", "Disable (read-only)")],
        required=True, default="invisible",
        help="Hide removes the element entirely. Disable leaves it visible but "
             "inert - useful when its absence would be confusing. Buttons are "
             "always hidden rather than disabled.")
    condition = fields.Char(
        string="Condition",
        help="Optional Python attribute expression. When set, the element is "
             "hidden/disabled only when the expression is truthy on the record.")

    @api.onchange("element_type")
    def _onchange_element_type(self):
        # A gentle hint so the placeholder matches the selected selector kind.
        hints = {
            "button": "action_confirm",
            "page": "Other Information",
            "filter": "my_filter",
            "groupby": "group_by_state",
            "kanban": "oe_kanban_action",
            "xpath": "//button[@name='action_confirm']",
        }
        if self.element_type and not self.selector:
            self.selector = hints.get(self.element_type, "")
