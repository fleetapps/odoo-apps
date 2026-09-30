from odoo import api, fields, models

# code, name, action, icon
DEFAULT_VARIANCE_REASONS = [
    ("breakage", "Breakage", "write_off", "fa-chain-broken"),
    ("spillage", "Spillage", "write_off", "fa-tint"),
    ("complimentary", "Complimentary", "write_off", "fa-gift"),
    ("staff", "Staff drink", "write_off", "fa-user"),
    ("missed_move", "Missed move", "move", "fa-exchange"),
    ("miscount", "Counting mistake", "recount", "fa-pencil"),
    ("pos_error", "POS error", "write_off", "fa-desktop"),
    ("unexplained", "Unexplained", "write_off", "fa-question"),
]


class OdinBarVarianceReason(models.Model):
    """Why a counted quantity differs from the expected one.

    The stock controller gives every difference a reason before the day is
    approved. Most reasons only record why (the difference is written off at
    approval, and reports group losses by reason); two of them fix the
    difference instead: a missed move records the transfer that was not
    logged, a counting mistake reopens the line to count it again.
    """

    _name = "odin.bar.variance.reason"
    _description = "Bar Variance Reason"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="Technical key, e.g. breakage.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    action = fields.Selection(
        [
            ("write_off", "Record and write off"),
            ("move", "Log the missed move"),
            ("recount", "Count the line again"),
        ],
        required=True,
        default="write_off",
        help="Record and write off: the difference is posted as a loss or gain at "
        "approval, under this reason.\nLog the missed move: the Desk asks for the "
        "transfer that was not logged and records it.\nCount the line again: the Desk "
        "opens the keypad to correct the count.",
    )
    icon = fields.Char(default="fa-question", help="Font Awesome icon shown on the Desk.")

    _code_company_uniq = models.Constraint(
        "UNIQUE(code, company_id)", "Variance reason codes must be unique per company."
    )

    @api.model
    def _create_default_reasons(self, company):
        """Create the standard reasons for ``company`` if it has none."""
        if self.with_context(active_test=False).search_count(
            [("company_id", "=", company.id)], limit=1
        ):
            return self.browse()
        return self.create(
            [
                {
                    "code": code,
                    "name": name,
                    "action": action,
                    "icon": icon,
                    "sequence": sequence * 10,
                    "company_id": company.id,
                }
                for sequence, (code, name, action, icon) in enumerate(DEFAULT_VARIANCE_REASONS, start=1)
            ]
        )

    def action_create_default_reasons(self):
        self._create_default_reasons(self.env.company)
        return True
