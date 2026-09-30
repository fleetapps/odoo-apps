from odoo import _, api, fields, models

# code, name, operation, operation type sequence code, scrap tag names (first
# one is created when none exists), asks for a member, default unit, icon
DEFAULT_REASONS = [
    ("roma", "Roma", "picking", "ROMA", (), False, "bottle", "fa-sign-out"),
    ("event", "Event", "picking", "EVT", (), False, "bottle", "fa-calendar"),
    ("debt", "Unpaid bill", "picking", "DEBT", (), True, "unit", "fa-user-times"),
    ("transfer", "To another bar", "transfer", "IBT", (), False, "bottle", "fa-exchange"),
]


class OdinBarReason(models.Model):
    """Where stock can go when it leaves the club's locations other than as a
    sale (Roma, an event, an unpaid bill): the destinations of a move logged
    on the Desk. The "To another bar" row only names the operation type of
    bar-to-bar moves."""

    _name = "odin.bar.reason"
    _description = "Bar Stock-out Reason"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="Technical key, e.g. roma or breakage.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    operation = fields.Selection(
        [
            ("picking", "Issue out"),
            ("scrap", "Scrap"),
            ("return", "Back to store"),
            ("transfer", "To another bar"),
        ],
        required=True,
        default="picking",
        help="Issue out: a validated transfer of the operation type below, from "
        "the bar.\nScrap: a scrap from the bar with the scrap reasons below.\n"
        "Back to store: a validated return with the bar's return type.\n"
        "To another bar: a validated transfer of the operation type below to the "
        "bar staff pick; it shows in that bar's deliveries.",
    )
    picking_type_id = fields.Many2one(
        "stock.picking.type",
        string="Operation type",
        check_company=True,
        help="The source location is always the bar the Desk runs on.",
    )
    scrap_tag_ids = fields.Many2many("stock.scrap.reason.tag", string="Scrap reasons")
    require_member = fields.Boolean(
        "Ask for member", help="Staff must enter the member name or number."
    )
    default_unit = fields.Selection(
        [("unit", "Stock unit (tot)"), ("bottle", "Bottle")],
        required=True,
        default="bottle",
        help="Unit the keypad starts in for spirits.",
    )
    icon = fields.Char(default="fa-sign-out", help="Font Awesome icon shown on the tile.")
    setup_issue = fields.Char(compute="_compute_setup_issue")

    _code_company_uniq = models.Constraint(
        "UNIQUE(code, company_id)", "Reason codes must be unique per company."
    )

    @api.depends("operation", "picking_type_id", "scrap_tag_ids")
    def _compute_setup_issue(self):
        for reason in self:
            issue = False
            if reason.operation in ("picking", "transfer") and not reason.picking_type_id:
                issue = _("Choose an operation type.")
            elif reason.operation == "scrap" and not reason.scrap_tag_ids:
                issue = _("Choose a scrap reason.")
            reason.setup_issue = issue

    @api.model
    def _create_default_reasons(self, company):
        """Create the standard reasons for ``company`` if it has none, mapped
        to the operation types (by sequence code) and scrap reasons (by name)
        that already exist."""
        if self.with_context(active_test=False).search_count(
            [("company_id", "=", company.id)], limit=1
        ):
            return self.browse()
        PickingType = self.env["stock.picking.type"]
        Tag = self.env["stock.scrap.reason.tag"]
        vals_list = []
        for sequence, (code, name, operation, type_code, tag_names, member, unit, icon) in enumerate(
            DEFAULT_REASONS, start=1
        ):
            vals = {
                "code": code,
                "name": name,
                "operation": operation,
                "require_member": member,
                "default_unit": unit,
                "icon": icon,
                "sequence": sequence * 10,
                "company_id": company.id,
            }
            if type_code:
                ptype = PickingType.search(
                    [("company_id", "=", company.id), ("sequence_code", "=", type_code)], limit=1
                )
                vals["picking_type_id"] = ptype.id
            if tag_names:
                tag = Tag.browse()
                for tag_name in tag_names:
                    tag = Tag.search([("name", "=ilike", tag_name)], limit=1)
                    if tag:
                        break
                vals["scrap_tag_ids"] = [(6, 0, (tag or Tag.create({"name": tag_names[0]})).ids)]
            vals_list.append(vals)
        return self.create(vals_list)

    def action_create_default_reasons(self):
        self._create_default_reasons(self.env.company)
        return True
