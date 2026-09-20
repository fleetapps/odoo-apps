# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
from odoo import api, fields, models

PARAM_TOKEN = "crm.3cx.auth"  # the upstream module's key, kept so an existing value survives
PARAM_MISSED_ACTIVITY = "crm.3cx.missed_call_activity"
PARAM_LEAD_ON_UNKNOWN = "crm.3cx.lead_on_unknown"
# Telesales automation
PARAM_FIRST_CALL_MINUTES = "crm.3cx.first_call_minutes"   # speed-to-lead SLA; 0 = off
PARAM_SLA_ESCALATE = "crm.3cx.sla_escalate"               # tell the team leader when it is breached
PARAM_CADENCE_HOURS = "crm.3cx.cadence_hours"             # "2,24,72,168": next attempt after 2 h, 1 d, 3 d, 7 d
PARAM_MAX_ATTEMPTS = "crm.3cx.max_attempts"               # unanswered attempts before Unreachable; 0 = never
PARAM_ADVANCE_STAGE = "crm.3cx.advance_stage"             # first conversation leaves the first stage
PARAM_POP_OPPORTUNITY = "crm.3cx.pop_opportunity"         # ring-time popup shows the open opportunity
PARAM_RENEWAL_DAYS = "crm.3cx.renewal_days"               # renewal leads this many days ahead; 0 = off
PARAM_WEBCLIENT_URL = "crm.3cx.webclient_url"             # https://pbx.example.com: Call buttons open its dialer
ALL_PARAMS = (
    PARAM_TOKEN, PARAM_MISSED_ACTIVITY, PARAM_LEAD_ON_UNKNOWN, PARAM_FIRST_CALL_MINUTES, PARAM_SLA_ESCALATE,
    PARAM_CADENCE_HOURS, PARAM_MAX_ATTEMPTS, PARAM_ADVANCE_STAGE, PARAM_POP_OPPORTUNITY, PARAM_RENEWAL_DAYS,
    PARAM_WEBCLIENT_URL,
)
DEFAULTS = {
    PARAM_MISSED_ACTIVITY: True,
    PARAM_LEAD_ON_UNKNOWN: False,
    PARAM_FIRST_CALL_MINUTES: 15,
    PARAM_SLA_ESCALATE: True,
    PARAM_CADENCE_HOURS: "2,24,72,168",
    PARAM_MAX_ATTEMPTS: 5,
    PARAM_ADVANCE_STAGE: True,
    PARAM_POP_OPPORTUNITY: True,
    PARAM_RENEWAL_DAYS: 30,
}


def param_flag(env, key, default=None):
    """A boolean stored as the strings "True"/"False" (see set_values)."""
    if default is None:
        default = DEFAULTS[key]
    return env["ir.config_parameter"].sudo().get_param(key, "True" if default else "False") == "True"


def param_int(env, key):
    value = env["ir.config_parameter"].sudo().get_param(key, str(DEFAULTS[key]))
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULTS[key]


def dial_url(env, number):
    """Where a Call button sends the browser: the 3CX Web Client's dialer,
    pre-filled, when the PBX URL is configured; otherwise a tel: link for
    whatever softphone the machine registers (the 3CX desktop app does)."""
    number = (number or "").strip()
    if not number:
        return False
    base = (env["ir.config_parameter"].sudo().get_param(PARAM_WEBCLIENT_URL) or "").strip().rstrip("/")
    if base:
        return "%s/webclient/#/call?phone=%s" % (base, number.replace("+", "%2B").replace(" ", ""))
    return "tel:" + number.replace(" ", "")


def param_hours(env, key):
    """A comma-separated list of hours, e.g. "2,24,72,168"."""
    raw = env["ir.config_parameter"].sudo().get_param(key, DEFAULTS[key]) or ""
    hours = []
    for part in raw.split(","):
        try:
            hours.append(float(part))
        except ValueError:
            continue
    return hours


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    token_3cx_crm = fields.Char(
        string="3CX API Key",
        config_parameter=PARAM_TOKEN,
        help="Shared secret that 3CX sends in the `apikey` header. Paste the same "
        "value into the API key parameter of the template in the 3CX Admin Console.",
    )
    threecx_webclient_url = fields.Char(
        string="3CX Web Client URL",
        config_parameter=PARAM_WEBCLIENT_URL,
        help="Your PBX address, e.g. https://company.3cx.ke. The Call buttons on leads "
        "and contacts then open the 3CX Web Client with the number ready to dial, in "
        "any browser. Leave empty to use tel: links (3CX desktop app or browser extension).",
    )
    # None of the fields below use `config_parameter=`: a Boolean or Integer
    # stored that way is deleted when set to False/0 and falls back to the field
    # default on the next read, so a toggle whose default is on could never be
    # switched off and an SLA could never be set to "off" (0). Stored as text.
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
    threecx_first_call_minutes = fields.Integer(
        string="First call within (minutes)",
        help="Every new lead with a phone number gets a First call activity, and is "
        "flagged as breached when nobody has dialled it within this time. 0 switches it off.",
    )
    threecx_sla_escalate = fields.Boolean(
        string="Tell the team leader when a first call is overdue",
    )
    threecx_cadence_hours = fields.Char(
        string="Retry cadence (hours)",
        help="After an unanswered attempt, the next attempt is scheduled this many "
        "hours later: one value per attempt, e.g. 2,24,72,168 = 2 h, 1 day, 3 days, 1 week.",
    )
    threecx_max_attempts = fields.Integer(
        string="Mark unreachable after (attempts)",
        help="A lead with this many unanswered attempts and no conversation is marked "
        "lost with the reason Unreachable. 0 never gives up.",
    )
    threecx_advance_stage = fields.Boolean(
        string="Move a lead out of its first stage after the first conversation",
    )
    threecx_pop_opportunity = fields.Boolean(
        string="Show the caller's open opportunity in the 3CX popup",
        help="When a caller has an open lead or opportunity, 3CX shows its title and "
        "stage and the link opens it instead of the contact card.",
    )
    threecx_renewal_days = fields.Integer(
        string="Create renewal leads (days ahead)",
        help="Contacts with a renewal date get a renewal lead this many days before it, "
        "assigned to their salesperson. 0 switches it off.",
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        env = self.env
        res.update(
            threecx_missed_call_activity=param_flag(env, PARAM_MISSED_ACTIVITY),
            threecx_lead_on_unknown=param_flag(env, PARAM_LEAD_ON_UNKNOWN),
            threecx_first_call_minutes=param_int(env, PARAM_FIRST_CALL_MINUTES),
            threecx_sla_escalate=param_flag(env, PARAM_SLA_ESCALATE),
            threecx_cadence_hours=env["ir.config_parameter"].sudo().get_param(PARAM_CADENCE_HOURS, DEFAULTS[PARAM_CADENCE_HOURS]),
            threecx_max_attempts=param_int(env, PARAM_MAX_ATTEMPTS),
            threecx_advance_stage=param_flag(env, PARAM_ADVANCE_STAGE),
            threecx_pop_opportunity=param_flag(env, PARAM_POP_OPPORTUNITY),
            threecx_renewal_days=param_int(env, PARAM_RENEWAL_DAYS),
        )
        return res

    def set_values(self):
        super().set_values()
        icp = self.env["ir.config_parameter"].sudo()
        flag = lambda v: "True" if v else "False"
        icp.set_param(PARAM_MISSED_ACTIVITY, flag(self.threecx_missed_call_activity))
        icp.set_param(PARAM_LEAD_ON_UNKNOWN, flag(self.threecx_lead_on_unknown))
        icp.set_param(PARAM_FIRST_CALL_MINUTES, str(max(0, self.threecx_first_call_minutes)))
        icp.set_param(PARAM_SLA_ESCALATE, flag(self.threecx_sla_escalate))
        icp.set_param(PARAM_CADENCE_HOURS, (self.threecx_cadence_hours or "").strip() or DEFAULTS[PARAM_CADENCE_HOURS])
        icp.set_param(PARAM_MAX_ATTEMPTS, str(max(0, self.threecx_max_attempts)))
        icp.set_param(PARAM_ADVANCE_STAGE, flag(self.threecx_advance_stage))
        icp.set_param(PARAM_POP_OPPORTUNITY, flag(self.threecx_pop_opportunity))
        icp.set_param(PARAM_RENEWAL_DAYS, str(max(0, self.threecx_renewal_days)))
