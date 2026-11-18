# -*- coding: utf-8 -*-
"""Filter suggestions: the right filters for *this* dashboard.

A purchasing board should be offered Vendor and Product, a sales board
Customer, a petty-cash board Payee — and a board of accounting summary cards
no Period at all, because every card already has one. Each suggestion must
also be something the validator accepts as it stands, or the assistant that
copies it gets refused.
"""
import json

from odoo.tests import TransactionCase, tagged

from ..models import ai_dashboard_spec as spec_lib
from ..models import ai_dashboard_suggest as suggest_lib


def m2o(relation, string, store=True):
    return {"type": "many2one", "relation": relation, "string": string,
            "searchable": True, "store": store}


def sel(values, string):
    return {"type": "selection", "selection": [[v, v.title()] for v in values],
            "string": string, "searchable": True, "store": True}


def dt(string="Date", kind="datetime"):
    return {"type": kind, "string": string, "searchable": True, "store": True}


PO_STATES = ["draft", "sent", "purchase", "done", "cancel"]
SO_STATES = ["draft", "sent", "sale", "cancel"]
MOVE_STATES = ["draft", "posted", "cancel"]
METADATA = {
    "purchase.order": {
        "partner_id": m2o("res.partner", "Vendor"),
        "user_id": m2o("res.users", "Buyer"),
        "company_id": m2o("res.company", "Company"),
        "currency_id": m2o("res.currency", "Currency"),
        "create_uid": m2o("res.users", "Created by"),
        "picking_type_id": m2o("stock.picking.type", "Deliver To"),
        "state": sel(PO_STATES, "Status"),
        "date_order": dt("Order Deadline"),
        "create_date": dt("Created on"),
    },
    "purchase.order.line": {
        "partner_id": m2o("res.partner", "Partner"),
        "product_id": m2o("product.product", "Product"),
        "order_id": m2o("purchase.order", "Order Reference"),
        "product_uom": m2o("uom.uom", "Unit"),
        "company_id": m2o("res.company", "Company"),
        "state": sel(PO_STATES, "Status"),
        "date_order": dt("Order Date"),
        "create_date": dt("Created on"),
    },
    "sale.order": {
        "partner_id": m2o("res.partner", "Customer"),
        "partner_shipping_id": m2o("res.partner", "Delivery Address"),
        "partner_invoice_id": m2o("res.partner", "Invoice Address"),
        "user_id": m2o("res.users", "Salesperson"),
        "team_id": m2o("crm.team", "Sales Team"),
        "state": sel(SO_STATES, "Status"),
        "date_order": dt("Order Date"),
        "create_date": dt("Created on"),
    },
    "sale.order.line": {
        "order_partner_id": m2o("res.partner", "Customer"),
        "product_id": m2o("product.product", "Product"),
        "salesman_id": m2o("res.users", "Salesperson"),
        "state": sel(SO_STATES, "Order Status"),
        "create_date": dt("Created on"),
    },
    "account.move": {
        "partner_id": m2o("res.partner", "Partner"),
        "commercial_partner_id": m2o("res.partner", "Commercial Entity"),
        "journal_id": m2o("account.journal", "Journal"),
        "state": sel(MOVE_STATES, "Status"),
        "payment_state": sel(["not_paid", "partial", "paid"], "Payment"),
        "date": dt("Date", "date"),
        "invoice_date_due": dt("Due Date", "date"),
    },
    "account.move.line": {
        "partner_id": m2o("res.partner", "Partner"),
        "account_id": m2o("account.account", "Account"),
        "journal_id": m2o("account.journal", "Journal"),
        "move_id": m2o("account.move", "Journal Entry"),
        "parent_state": sel(MOVE_STATES, "Status"),
        "date": dt("Date", "date"),
        "distribution_analytic_account_ids": {
            "type": "many2many", "relation": "account.analytic.account",
            "string": "Analytic Accounts", "searchable": True, "store": False},
    },
    "account.journal": {
        "company_id": m2o("res.company", "Company"),
        "create_date": dt("Created on"),
    },
    "account.analytic.line": {
        "partner_id": m2o("res.partner", "Partner"),
        "account_id": m2o("account.analytic.account", "Project"),
        "x_plan2_id": m2o("account.analytic.account", "Vehicle"),
        "date": dt("Date", "date"),
    },
    "x.fleet.log": {
        "x_driver_id": m2o("res.users", "Driver"),
        "x_owner_id": m2o("res.users", "Owner"),
        "create_date": dt("Created on"),
    },
}
PARTNER_FIELDS = {"supplier_rank", "customer_rank", "name"}
ANALYTIC_FIELDS = {"name", "plan_id", "root_plan_id"}


def tile(wid, model, kind="bar", group_by=None, domain=None, **extra):
    widget = {"id": wid, "type": kind, "title": wid, "span": 6,
              "query": {"model": model, "domain": domain or [],
                        "group_by": group_by if group_by is not None else (
                            [] if kind in ("kpi", "pnl", "aging", "banks")
                            else ["create_date:month"]),
                        "measures": ["__count"]}}
    widget.update(extra)
    return widget


def board(*widgets, filters=None):
    return {"schema": spec_lib.SCHEMA_ID, "title": "Board",
            "filters": filters or [], "widgets": list(widgets)}


def run(spec, **kwargs):
    kwargs.setdefault("comodel_fields", {"res.partner": PARTNER_FIELDS,
                                         "account.analytic.account": ANALYTIC_FIELDS})
    return suggest_lib.suggest(spec, METADATA, **kwargs)


def by_label(result):
    return {s["filter"]["label"]: s for s in result["suggestions"]}


@tagged("post_install", "-at_install")
class TestSuggestPure(TransactionCase):
    """The suggester itself, over field descriptions — no database."""

    def test_a_purchasing_board_gets_period_vendor_and_product(self):
        result = run(board(tile("orders", "purchase.order"),
                           tile("lines", "purchase.order.line")))
        labels = [s["filter"]["label"] for s in result["suggestions"]]
        self.assertEqual(labels[0], "Period")
        self.assertIn("Vendor", labels)
        self.assertIn("Product", labels)
        vendor = by_label(result)["Vendor"]
        self.assertEqual(vendor["filter"]["field"], "partner_id")
        self.assertEqual(vendor["filter"]["domain"], [["supplier_rank", ">", 0]])
        self.assertEqual(sorted(vendor["reaches"]), ["lines", "orders"])
        self.assertEqual(by_label(result)["Product"]["reaches"], ["lines"])

    def test_plumbing_is_never_offered(self):
        result = run(board(tile("orders", "purchase.order"),
                           tile("lines", "purchase.order.line")))
        fields = {s["filter"].get("field") for s in result["suggestions"]}
        for plumbing in ("currency_id", "create_uid", "product_uom",
                         "order_id", "company_id"):
            self.assertNotIn(plumbing, fields)

    def test_company_is_offered_only_with_several(self):
        spec = board(tile("orders", "purchase.order"))
        one = run(spec, companies=1, limit=20)
        two = run(spec, companies=2, limit=20)
        self.assertNotIn("Company", by_label(one))
        self.assertIn("Company", by_label(two))

    def test_the_same_customer_under_another_name_is_mapped(self):
        """order_partner_id on lines is partner_id on orders."""
        result = run(board(tile("orders", "sale.order"),
                           tile("lines", "sale.order.line")))
        customer = by_label(result)["Customer"]
        self.assertEqual(customer["filter"]["domain"], [["customer_rank", ">", 0]])
        main = customer["filter"]["field"]
        mapped = customer["filter"].get("fields", {})
        self.assertEqual({main} | set(mapped.values()),
                         {"partner_id", "order_partner_id"})
        self.assertEqual(sorted(customer["reaches"]), ["lines", "orders"])

    def test_addresses_do_not_compete_with_the_customer(self):
        result = run(board(tile("orders", "sale.order")), limit=20)
        fields = [s["filter"].get("field") for s in result["suggestions"]]
        self.assertIn("partner_id", fields)
        self.assertNotIn("partner_shipping_id", fields)

    def test_a_board_of_period_cards_gets_no_period(self):
        """Business at a Glance: every card has its own period or none."""
        spec = board(
            tile("pnl", "account.move.line", "pnl", period="this_year"),
            tile("ar", "account.move", "aging",
                 domain=[["move_type", "in", ["out_invoice", "out_refund"]]]),
            tile("banks", "account.journal", "banks"),
            tile("sales", "sale.order", "line", ["date_order:month"],
                 period="this_year", caption="Sales"))
        result = run(spec, limit=20)
        self.assertNotIn("Period", by_label(result))
        self.assertTrue(any("own period" in n for n in result["notes"]))

    def test_a_cash_book_of_mixed_payees_gets_the_neutral_word(self):
        result = run(board(
            tile("spend", "account.move.line", "kpi",
                 domain=[["journal_id", "=", 10]]),
            tile("vehicles", "account.analytic.line", "bar", ["x_plan2_id"])))
        labels = by_label(result)
        self.assertIn("Partner", labels)
        self.assertNotIn("domain", labels["Partner"]["filter"])
        self.assertIn("Vehicle", labels)

    def test_each_analytic_plan_is_its_own_filter(self):
        """Project and Vehicles both point at analytic accounts; they are
        still two questions."""
        result = run(board(tile("lines", "account.analytic.line", "bar",
                                ["x_plan2_id"])), limit=20)
        fields = [s["filter"].get("field") for s in result["suggestions"]]
        self.assertIn("x_plan2_id", fields)
        self.assertIn("account_id", fields)

    def test_a_plan_reaches_journal_items_and_offers_only_its_accounts(self):
        """Petty cash: picking a car narrows the spend, not only the
        analytic tile."""
        result = run(board(
            tile("spend", "account.move.line", "kpi"),
            tile("vehicles", "account.analytic.line", "bar", ["x_plan2_id"],
                 domain=[["x_plan2_id", "!=", False]])), limit=20)
        vehicle = by_label(result)["Vehicle"]
        self.assertEqual(vehicle["filter"]["field"], "x_plan2_id")
        self.assertEqual(vehicle["filter"]["fields"],
                         {"account.move.line": "distribution_analytic_account_ids"})
        self.assertEqual(vehicle["filter"]["domain"], [["root_plan_id", "=", 2]])
        self.assertEqual(sorted(vehicle["reaches"]), ["spend", "vehicles"])

    def test_bills_mean_vendors(self):
        result = run(board(tile("bills", "account.move", "kpi", domain=[
            ["move_type", "in", ["in_invoice", "in_refund"]]])))
        self.assertIn("Vendor", by_label(result))

    def test_two_equally_likely_fields_are_not_guessed(self):
        result = run(board(tile("log", "x.fleet.log")), limit=20)
        self.assertFalse(any(s["filter"].get("field") in ("x_driver_id", "x_owner_id")
                             for s in result["suggestions"]))
        self.assertTrue(any("x_driver_id" in n for n in result["notes"]))

    def test_a_field_with_one_value_in_the_data_is_left_out(self):
        spec = board(tile("orders", "purchase.order"))
        result = run(spec, distinct={("purchase.order", "partner_id"): 1})
        self.assertNotIn("Vendor", by_label(result))

    def test_status_is_only_offered_where_the_values_agree(self):
        """'posted' means nothing to a sale order."""
        result = run(board(tile("orders", "sale.order"),
                           tile("bills", "account.move")), limit=20)
        status = [s for s in result["suggestions"]
                  if s["filter"].get("field") == "state"]
        self.assertEqual(len(status), 1)
        self.assertEqual(len(status[0]["reaches"]), 1)

    def test_a_status_the_tile_already_pins_is_not_offered(self):
        result = run(board(tile("orders", "sale.order", domain=[
            ["state", "=", "sale"]])), limit=20)
        self.assertFalse(any(s["filter"].get("field") == "state"
                             for s in result["suggestions"]))

    def test_what_is_already_there_is_not_offered_again(self):
        spec = board(tile("orders", "purchase.order"), filters=[
            {"key": "period", "type": "date_range", "default": "this_month"},
            {"key": "vendor", "type": "many2one", "field": "partner_id"}])
        labels = by_label(run(spec))
        self.assertNotIn("Period", labels)
        self.assertNotIn("Vendor", labels)

    def test_every_suggestion_is_accepted_by_the_validator(self):
        for spec in (
                board(tile("o", "purchase.order"), tile("l", "purchase.order.line")),
                board(tile("o", "sale.order"), tile("l", "sale.order.line")),
                board(tile("s", "account.move.line", "kpi"),
                      tile("v", "account.analytic.line", "bar", ["x_plan2_id"]))):
            filters = [s["filter"] for s in run(spec, limit=20)["suggestions"]]
            self.assertTrue(filters)
            checked = spec_lib.validate(dict(spec, filters=filters))
            self.assertEqual(len(checked["filters"]), len(filters))
            self.assertEqual(len({f["key"] for f in filters}), len(filters))


@tagged("post_install", "-at_install")
class TestSuggestTool(TransactionCase):
    """The MCP tool, as an assistant calls it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Engine = cls.env["mcp.engine"]
        partner_model = cls.env["ir.model"]._get("res.partner")
        cls.scope = cls.env["mcp.scope"].create({
            "name": "TEST suggest filters", "read_only": False,
            "require_approval": False, "rate_limit_per_hour": 0,
            "line_ids": [(0, 0, {"model_id": partner_model.id,
                                 "can_read": True})],
        })
        kenya, uganda = cls.env.ref("base.ke"), cls.env.ref("base.ug")
        cls.env["res.partner"].create([
            {"name": "AID Suggest KE", "country_id": kenya.id},
            {"name": "AID Suggest UG", "country_id": uganda.id},
        ])

    def _call(self, name, args):
        result = self.Engine.call_tool(self.scope, name, args, {})
        return json.loads(result["content"][0]["text"])

    def _spec(self):
        spec = board(tile("people", "res.partner", "kpi"))
        spec["filters"] = []
        return spec

    def test_the_tool_suggests_a_period_and_real_fields(self):
        out = self._call("suggest_dashboard_filters", {"spec": self._spec()})
        filters = [s["filter"] for s in out["suggestions"]]
        self.assertEqual(filters[0]["type"], "date_range")
        self.assertIn("country_id", [f.get("field") for f in filters])
        self.assertTrue(out["how_to_use"])

    def test_suggestions_validate_against_the_real_database(self):
        out = self._call("suggest_dashboard_filters", {"spec": self._spec()})
        spec = dict(self._spec(), filters=[s["filter"] for s in out["suggestions"]])
        spec_lib.validate(spec, self.env)

    def test_it_works_from_a_saved_dashboard(self):
        board_record = self.env["ai.dashboard"].create({
            "name": "Suggest me", "spec_json": json.dumps(self._spec()),
            "state": "published"})
        out = self._call("suggest_dashboard_filters",
                         {"dashboard_id": board_record.id})
        self.assertTrue(out["suggestions"])
        read = self._call("get_dashboard", {"dashboard_id": board_record.id})
        self.assertTrue(read["filter_suggestions"])

    def test_a_preview_carries_suggestions(self):
        out = self._call("preview_dashboard", {"spec": self._spec()})
        self.assertTrue(out["filter_suggestions"])
        self.assertIn("filters", out["message"])

    def test_it_is_a_reading_tool(self):
        self.assertNotIn("suggest_dashboard_filters",
                         self.env["mcp.tool"]._write_handlers())
