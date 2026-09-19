# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
from odoo import api, fields, models

PARAM_TOKEN = "crm.3cx.auth"  # the upstream module's key, kept so an existing value survives
PARAM_MISSED_ACTIVITY = "crm.3cx.missed_call_activity"
PARAM_LEAD_ON_UNKNOWN = "crm.3cx.lead_on_unknown"
ALL_PARAMS = (PARAM_TOKEN, PARAM_MISSED_ACTIVITY, PARAM_LEAD_ON_UNKNOWN)


def param_flag(env, key, default):
    """A boolean stored as the strings "True"/"False" (see set_values)."""
    return env["ir.config_parameter"].sudo().get_param(key, "True" if default else "False") == "True"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    token_3cx_crm = fields.Char(
        string="3CX API Key",
        config_parameter=PARAM_TOKEN,
        help="Shared secret that 3CX sends in the `apikey` header. Paste the same "
        "value into the API key parameter of the template in the 3CX Admin Console.",
    )
    # Not `config_parameter=`: a Boolean stored that way is deleted when set to
    # False and falls back to the field default on the next read, so a toggle
    # whose default is True could never be switched off. Stored as "True"/"False".
    threecx_missed_call_activity = fields.Boolean(
        string="Schedule a call-back activity for missed calls",
        help="Assigned to the record's salesperson, else to the agent who missed "
        "the call when the 3CX extension email matches an Odoo user. The next "
        "answered call with that contact marks it done.",
    )
    threecx_lead_on_unknown = fields.Boolean(
        string="Create a lead for calls and chats from unknown numbers",
        help="An inbound call or chat from a number or email that matches neither "
        "a contact nor a lead creates a lead named after it.",
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        res.update(
            threecx_missed_call_activity=param_flag(self.env, PARAM_MISSED_ACTIVITY, True),
            threecx_lead_on_unknown=param_flag(self.env, PARAM_LEAD_ON_UNKNOWN, False),
        )
        return res

    def set_values(self):
        super().set_values()
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(PARAM_MISSED_ACTIVITY, "True" if self.threecx_missed_call_activity else "False")
        icp.set_param(PARAM_LEAD_ON_UNKNOWN, "True" if self.threecx_lead_on_unknown else "False")
