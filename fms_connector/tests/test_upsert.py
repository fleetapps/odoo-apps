# -*- coding: utf-8 -*-
# Part of Fleet FMS Connector. License OPL-1.
"""TransactionCase tests for the upsert logic the controller calls.
No live HTTP: https://www.odoo.com/documentation/19.0/developer/reference/backend/testing.html
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestPurchaseRequestUpsert(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        cls.picking_type = cls.env["stock.picking.type"].search(
            [("code", "=", "incoming")], limit=1
        )
        cls.product = cls.env["product.product"].create({
            "name": "Vehicle Maintenance & Repair Service",
            "type": "service",
            "purchase_ok": True,
            "can_be_expensed": True,
        })
        cls.requestor = cls.env.ref("base.user_admin")
        cls.approver = cls.env.ref("base.user_admin")
        icp.set_param(
            "fms_connector.default_picking_type_id", cls.picking_type.id
        )
        icp.set_param(
            "fms_connector.default_request_product_id", cls.product.id
        )
        icp.set_param(
            "fms_connector.default_expense_product_id", cls.product.id
        )
        icp.set_param(
            "fms_connector.default_requestor_id", cls.requestor.id
        )
        icp.set_param(
            "fms_connector.default_approver_id", cls.approver.id
        )
        icp.set_param(
            "fms_connector.default_expense_employee_id", ""
        )

    def test_create_then_retry_is_idempotent(self):
        payload = {
            "fms_ref": "fms-pr-001",
            "requested_by_email": "nobody-matches@example.invalid",
            "requested_by_name": "Jane Driver",
            "country": "Kenya",
            "branch": "Nairobi Program Office",
            "vehicle_ref": "KDA 123B",
            "description": "Brake pads replacement",
            "estimated_cost": 450.0,
        }
        pr1 = self.env["purchase.request"].fms_upsert(payload)
        self.assertEqual(pr1.x_fms_ref, "fms-pr-001")
        self.assertEqual(pr1.x_fms_vehicle_ref, "KDA 123B")
        self.assertEqual(len(pr1.line_ids), 1)

        payload["description"] = "Brake pads + oil change"
        pr2 = self.env["purchase.request"].fms_upsert(payload)
        self.assertEqual(pr1.id, pr2.id, "retry must update, not duplicate")
        self.assertEqual(pr2.line_ids.name, "Brake pads + oil change")

    def test_missing_fms_ref_raises(self):
        with self.assertRaises(UserError):
            self.env["purchase.request"].fms_upsert({})

    def test_no_requestor_match_and_no_fallback_raises(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "fms_connector.default_requestor_id", ""
        )
        with self.assertRaises(UserError):
            self.env["purchase.request"].fms_upsert({
                "fms_ref": "fms-pr-002",
                "requested_by_email": "nobody-matches@example.invalid",
            })


class TestExpenseUpsert(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        cls.product = cls.env["product.product"].create({
            "name": "Vehicle Maintenance & Repair",
            "type": "service",
            "can_be_expensed": True,
        })
        cls.employee = cls.env["hr.employee"].create({
            "name": "Fleet Ops Fallback",
        })
        icp.set_param(
            "fms_connector.default_expense_product_id", cls.product.id
        )
        icp.set_param(
            "fms_connector.default_expense_employee_id", cls.employee.id
        )

    def test_create_submits_for_approval(self):
        payload = {
            "fms_ref": "fms-jc-001",
            "employee_email": "nobody-matches@example.invalid",
            "vehicle_ref": "KDA 123B",
            "vendor_name": "Acme Motors",
            "description": "Brake pad replacement",
            "amount": 430.0,
            "currency": "KES",
        }
        exp1 = self.env["hr.expense"].fms_upsert(payload)
        self.assertEqual(exp1.employee_id, self.employee)
        self.assertEqual(exp1.total_amount_currency, 430.0)
        # fms_upsert submits on create (_fms_submit_for_approval) so Rose is
        # actually notified -- a bare create() would leave it an invisible
        # 'draft' forever (see hr_expense.py). Auto-validation may fast-track
        # this straight to 'approved' when the fallback employee has no
        # expense manager configured; either way it must not still be draft.
        self.assertNotEqual(exp1.state, "draft")

    def test_retry_after_submit_is_a_no_op_not_an_overwrite(self):
        """Once Odoo has moved the expense past 'draft' (submitted/approved),
        a same-fms_ref retry must return that record as-is, not silently
        rewrite its amount -- Rose may already be looking at it."""
        payload = {
            "fms_ref": "fms-jc-002",
            "employee_email": "nobody-matches@example.invalid",
            "amount": 430.0,
            "currency": "KES",
        }
        exp1 = self.env["hr.expense"].fms_upsert(payload)
        self.assertNotEqual(exp1.state, "draft")

        payload["amount"] = 450.0
        exp2 = self.env["hr.expense"].fms_upsert(payload)
        self.assertEqual(exp1.id, exp2.id)
        self.assertEqual(exp2.total_amount_currency, 430.0, "must not be overwritten post-submit")
