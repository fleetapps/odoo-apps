"""The AI screens in a real browser, with the model mocked by feature."""

import json
import time

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import HttpCase, tagged

from .common import AiCase, message, tool_call


@tagged("post_install", "-at_install")
class TestAiTours(AiCase, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The browser is not frozen in time: the P&L opens on last month.
        cls.last_month = fields.Date.today().replace(day=1) - relativedelta(months=1)
        customer = cls.env["res.partner"].create({"name": "Tour Customer"})
        cls.post(cls.last_month.replace(day=10), [(cls.revenue, -1000.0, {"partner": customer})])
        cls.post((cls.last_month - relativedelta(months=1)).replace(day=10), [(cls.revenue, -600.0, {"partner": customer})])
        cls.env["account.bank.statement.line"].create({
            "journal_id": cls.company_data["default_journal_bank"].id, "date": cls.last_month.replace(day=12),
            "payment_ref": "TOUR TAXI", "amount": -25.0})
        # The accounting test base works in a company of its own: sign the
        # tours' user into it.
        cls.env.ref("base.user_admin").write({
            "company_ids": [(4, cls.env.company.id)],
            "company_id": cls.env.company.id,
            "group_ids": [(4, cls.env.ref("account.group_account_user").id)],
        })

    def respond(self, params):
        system = params["system"][0]["text"]
        if "categorise" in system:
            return message({"suggestions": [{"item": 0, "account_code": self.opex.code, "partner": None,
                                             "analytic_account": None, "confidence": 0.8,
                                             "reason": "Looks like travel."}]})
        if "explanation of one change" in system:
            facts = json.loads(params["messages"][0]["content"].split("(data): ", 1)[1])
            ref = facts["drivers"][0]["ref"]
            return message({"headline": "Revenue rose.", "caveats": [],
                            "points": [{"text": f"Tour Customer brought it [{ref}].", "evidence": [ref]}]})
        last = params["messages"][-1]["content"]
        if isinstance(last, str):
            end = self.last_month + relativedelta(months=1, days=-1)
            return tool_call("pnl", {"date_from": str(self.last_month), "date_to": str(end),
                                     "comparison": "none", "line_code": None})
        return message({"blocks": [
            {"type": "text", "text": "Revenue was 1,000 [E1].", "items": None,
             "columns": None, "rows": None, "value": None, "evidence": ["E1"]},
            # A supplier whose name starts with "Total", last, its small
            # figures nearly adding up the rows above...
            {"type": "table", "text": "Fuel suppliers", "items": None, "value": None, "evidence": [],
             "columns": ["Supplier", "Bills", "Amount (KES)"],
             "rows": [["Shell", "4", "500"], ["Rubis", "3", "300"], ["Total Kenya Ltd", "6", "798"]]},
            # ... a closing total that adds them up, beside a change in % that
            # does not (the % only in the header), with a reference in the
            # caption...
            {"type": "table", "text": "Revenue by customer [E1]", "items": None, "value": None, "evidence": ["E1"],
             "columns": ["Customer", "Revenue (KES)", "Change (%)"],
             "rows": [["Tour Customer", "1,000", "25.0"], ["Others", "400", "-10.0"],
                      ["Total revenue", "1,400", "9.3"]]},
            # ... and a total labelled after an empty code cell.
            {"type": "table", "text": "Revenue by account", "items": None, "value": None, "evidence": [],
             "columns": ["Code", "Account", "Amount (KES)"],
             "rows": [["400000", "Sales", "1,000"], ["400100", "Services", "400"], ["", "Total", "1,400"]]},
        ], "follow_ups": ["And the month before?"]})

    def test_review(self):
        self.mock_api(*[self.respond] * 5)
        self.env["odin.ai.suggestion"]._generate(self.env.company, interactive=True)
        self.start_tour("/odoo/action-odin_account_ai.odin_ai_review_action", "odin_account_ai_review_tour",
                        login="admin")

    def test_explain(self):
        self.mock_api(*[self.respond] * 5)
        self.start_tour("/odoo/action-odin_account_pnl.odin_pnl_report_action", "odin_account_ai_explain_tour",
                        login="admin")

    def test_ask(self):
        self.mock_api(*[self.respond] * 5)
        self.start_tour("/odoo/action-odin_account_ai.odin_ai_ask_action", "odin_account_ai_ask_tour",
                        login="admin")

    def respond_slowly(self, params):
        """The model taking two seconds over a tool step, so that the tour
        can act while the step runs."""
        if isinstance(params["messages"][-1]["content"], str):
            time.sleep(2)
        return self.respond(params)

    def test_ask_stop_and_switch(self):
        self.mock_api(*[self.respond_slowly] * 6)
        self.start_tour("/odoo/action-odin_account_ai.odin_ai_ask_action", "odin_account_ai_ask_stop_tour",
                        login="admin")

    def test_ask_about_a_line(self):
        self.mock_api(*[self.respond] * 4)
        self.start_tour("/odoo/action-odin_account_pnl.odin_pnl_report_action", "odin_account_ai_ask_line_tour",
                        login="admin")
        # The tour's conversations are the admin's own: read them as superuser.
        starter, line = self.env["odin.ai.conversation"].sudo().search(
            [("user_id", "=", self.env.ref("base.user_admin").id)], order="id desc", limit=2)[::-1]
        self.assertNotIn("Context from the page", starter.messages, "a starter goes without the line's context")
        self.assertIn("Context from the page", line.messages)

    def test_ask_from_the_command_palette(self):
        self.mock_api()  # a draft only: nothing is sent
        self.start_tour("/odoo/action-odin_account_pnl.odin_pnl_report_action", "odin_account_ai_ask_palette_tour",
                        login="admin")

