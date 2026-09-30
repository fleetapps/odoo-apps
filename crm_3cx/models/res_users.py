# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    threecx_extension = fields.Char(
        "3CX Extension", index=True,
        help="The extension this user answers on. Calls reported by 3CX for it are "
        "logged in their name and missed calls are assigned to them. Without it, "
        "the agent is matched on the email address configured in 3CX.",
    )

    _threecx_extension_unique = models.Constraint(
        "UNIQUE(threecx_extension)", "Another user already has this 3CX extension.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["threecx_extension"]
