from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    odin_ai_consent = fields.Boolean(
        string="Send accounting data to Anthropic",
        help="Questions, figures and the journal items the AI reads are sent to Anthropic's API "
             "to be answered. Nothing is sent until this is ticked.")
    odin_ai_monthly_tokens = fields.Integer(
        string="Monthly AI budget (tokens)",
        default=3000000,
        help="Input plus output tokens per calendar month for this company. 0: no limit.")
