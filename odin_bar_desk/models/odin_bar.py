from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

# Deliveries older than this drop off the Desk and the dashboard.
DELIVERY_DAYS = 14


class OdinBar(models.Model):
    _inherit = "odin.bar"

    device_user_ids = fields.One2many("res.users", "odin_bar_id", string="Desk logins")
    employee_ids = fields.Many2many(
        "hr.employee",
        "odin_bar_hr_employee_rel",
        "bar_id",
        "employee_id",
        string="Staff",
        groups="hr.group_hr_user",
        help="Employees who can sign in here with their PIN.",
    )
    sheet_product_ids = fields.Many2many(
        "product.product",
        "odin_bar_sheet_product_rel",
        "bar_id",
        "product_id",
        string="Count sheet",
        domain=[("bar_desk_ok", "=", True)],
        help="Products on this bar's closing count. Leave empty to count every "
        "product shown in the Bar Desk.",
    )
    currency_id = fields.Many2one(related="company_id.currency_id")

    desk_unchecked_count = fields.Integer("Not checked", compute="_compute_desk_dashboard")
    desk_to_approve_count = fields.Integer("To approve", compute="_compute_desk_dashboard")
    desk_dispute_count = fields.Integer("Open disputes", compute="_compute_desk_dashboard")
    desk_stock_out_today = fields.Integer("Stock outs today", compute="_compute_desk_dashboard")
    desk_last_closing_date = fields.Date("Last approved close", compute="_compute_desk_dashboard")
    desk_last_pos_date = fields.Date("Last POS day", compute="_compute_desk_dashboard")
    desk_variance_week = fields.Monetary(
        "Variance, 7 days", compute="_compute_desk_dashboard", currency_field="currency_id"
    )

    @api.model_create_multi
    def create(self, vals_list):
        bars = super().create(vals_list)
        for company in bars.company_id:
            self.env["odin.bar.reason"].sudo()._create_default_reasons(company)
        return bars

    def _compute_desk_dashboard(self):
        Picking = self.env["stock.picking"].sudo()
        Count = self.env["odin.bar.count"].sudo()
        Activity = self.env["odin.bar.activity"].sudo()
        PosDay = self.env["odin.bar.pos.day"].sudo()
        for bar in self:
            if not bar.id:
                bar.update(
                    {
                        "desk_unchecked_count": 0,
                        "desk_to_approve_count": 0,
                        "desk_dispute_count": 0,
                        "desk_stock_out_today": 0,
                        "desk_last_closing_date": False,
                        "desk_last_pos_date": False,
                        "desk_variance_week": 0.0,
                    }
                )
                continue
            today = bar._business_date()
            start, end = bar._business_day_bounds(today)
            last_close = Count.search(
                [("bar_id", "=", bar.id), ("kind", "=", "closing"), ("state", "=", "approved")],
                order="business_date desc",
                limit=1,
            )
            week = Count.search(
                [
                    ("bar_id", "=", bar.id),
                    ("state", "=", "approved"),
                    ("business_date", ">", today - timedelta(days=7)),
                ]
            )
            bar.update(
                {
                    "desk_unchecked_count": Picking.search_count(
                        bar._desk_delivery_domain() + [("bar_ack_state", "=", "not_checked")]
                    ),
                    "desk_to_approve_count": Count.search_count(
                        [("bar_id", "=", bar.id), ("state", "in", ("submitted", "recount"))]
                    ),
                    "desk_dispute_count": Picking.search_count(bar._desk_dispute_domain()),
                    "desk_stock_out_today": Activity.search_count(
                        [
                            ("bar_id", "=", bar.id),
                            ("kind", "=", "stock_out"),
                            ("date", ">=", start),
                            ("date", "<", end),
                        ]
                    ),
                    "desk_last_closing_date": last_close.business_date,
                    "desk_last_pos_date": PosDay.search(
                        [("bar_id", "=", bar.id)], order="business_date desc", limit=1
                    ).business_date,
                    "desk_variance_week": sum(week.mapped("diff_value")),
                }
            )

    # ------------------------------------------------------------------
    # Domains and product lists shared by the Desk and the backend
    # ------------------------------------------------------------------

    def _desk_delivery_domain(self, days=DELIVERY_DAYS):
        """Recent transfers into this bar that staff can check."""
        self.ensure_one()
        return [
            ("location_dest_id", "=", self.location_id.id),
            ("state", "=", "done"),
            ("bar_ack_state", "!=", False),
            ("date_done", ">=", fields.Datetime.now() - timedelta(days=days)),
        ]

    def _desk_dispute_domain(self):
        """Open disputes (draft corrections) touching this bar or store."""
        self.ensure_one()
        return [
            ("bar_dispute_origin_id", "!=", False),
            ("state", "not in", ("done", "cancel")),
            "|",
            ("location_id", "=", self.location_id.id),
            ("location_dest_id", "=", self.location_id.id),
        ]

    def _desk_products(self):
        """Products staff can pick at this bar, in count sheet order."""
        self.ensure_one()
        products = self.env["product.product"].search(
            [("bar_desk_ok", "=", True), ("company_id", "in", [self.company_id.id, False])]
        )
        return products.sorted(lambda product: product._bar_sort_key())

    def _desk_sheet_products(self):
        """Products on this bar's closing count, in count sheet order."""
        self.ensure_one()
        products = self.sheet_product_ids.filtered(lambda p: p.active and p.bar_desk_ok)
        if not products:
            return self._desk_products()
        return products.sorted(lambda product: product._bar_sort_key())

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def action_create_device_user(self):
        """Create the login a bar's shared phone or tablet stays signed in with."""
        self.ensure_one()
        login = f"{self.code.lower()}.desk"
        Users = self.env["res.users"]
        if Users.with_context(active_test=False).search_count([("login", "=", login)]):
            raise UserError(_("A user with the login %(login)s already exists.", login=login))
        user = Users.create(
            {
                "name": _("%(bar)s Desk", bar=self.name),
                "login": login,
                "company_id": self.company_id.id,
                "company_ids": [Command.set(self.company_id.ids)],
                "group_ids": [
                    Command.set(self.env.ref("odin_bar_desk.group_bar_desk_staff").ids)
                ],
                "odin_bar_id": self.id,
                "action_id": self.env.ref("odin_bar_desk.action_bar_desk").id,
                "tz": self.tz,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.users",
            "res_id": user.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def _desk_window_action(self, xmlid, domain, context=None):
        action = self.env["ir.actions.act_window"]._for_xml_id(xmlid)
        action["domain"] = domain
        action["context"] = context or {}
        return action

    def action_desk_counts(self):
        self.ensure_one()
        return self._desk_window_action(
            "odin_bar_desk.odin_bar_count_action",
            [("bar_id", "=", self.id)],
            {"search_default_to_approve": 1},
        )

    def action_desk_deliveries(self):
        self.ensure_one()
        return self._desk_window_action(
            "odin_bar_desk.action_bar_deliveries",
            [("location_dest_id", "=", self.location_id.id), ("bar_ack_state", "!=", False)],
            {"search_default_not_checked": 1},
        )

    def action_desk_disputes(self):
        self.ensure_one()
        return self._desk_window_action(
            "odin_bar_desk.action_bar_disputes", self._desk_dispute_domain()
        )

    def action_desk_stock_outs(self):
        self.ensure_one()
        return self._desk_window_action(
            "odin_bar_desk.odin_bar_activity_stock_out_action",
            [("bar_id", "=", self.id), ("kind", "=", "stock_out")],
        )
