from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    odin_bar_ids = fields.Many2many(
        "odin.bar",
        "odin_bar_hr_employee_rel",
        "employee_id",
        "bar_id",
        string="Bar Desk bars",
        groups="hr.group_hr_user",
        help="Bars where this employee can sign in to the Bar Desk with their PIN.",
    )
    odin_bar_all = fields.Boolean(
        "All bars",
        groups="hr.group_hr_user",
        help="Can sign in at every bar and store of the company, including bars added later.",
    )

    odin_bar_can_order = fields.Boolean(
        "Can order from suppliers",
        groups="hr.group_hr_user",
        help="Sees Order on the Main Store's Desk and can send purchase orders to suppliers.",
    )

    def _odin_bar_allowed(self, bar):
        """Whether this employee may sign in to the Desk at ``bar``."""
        self.ensure_one()
        employee = self.sudo()
        return bar in employee.odin_bar_ids or (
            employee.odin_bar_all and employee.company_id == bar.company_id
        )
