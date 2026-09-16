# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import timedelta

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestOdinLc(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # AccountTestInvoicingCommon's user is an accountant, not a project
        # manager. Grant it explicitly rather than creating projects as sudo,
        # so the tests exercise the same access path a real user would.
        cls.env.user.group_ids |= cls.env.ref("project.group_project_manager")
        cls.today = fields.Date.context_today(cls.env["odin.letter.of.credit"])
        cls.supplier = cls.env["res.partner"].create({"name": "Inverter Supplier"})
        cls.bank = cls.env["res.partner"].create({"name": "First Bank"})
        cls.project = cls.env["project.project"].create({"name": "BESS Phase 1"})
        cls.doc_invoice = cls.env.ref("odin_letter_of_credit.lc_doc_commercial_invoice")
        cls.doc_bl = cls.env.ref("odin_letter_of_credit.lc_doc_bill_of_lading")

    def _lc(self, **overrides):
        vals = {
            "direction": "import",
            "lc_type": "sight",
            "partner_id": self.supplier.id,
            "issuing_bank_id": self.bank.id,
            "project_id": self.project.id,
            "amount": 100000.0,
            "currency_id": self.company_data["currency"].id,
            "date_expiry": self.today + timedelta(days=90),
            "lc_reference": "ILC-0001",
            "document_ids": [
                Command.create({"document_type_id": self.doc_invoice.id}),
                Command.create({"document_type_id": self.doc_bl.id}),
            ],
        }
        vals.update(overrides)
        return self.env["odin.letter.of.credit"].create(vals)

    def _issue(self, lc):
        lc.action_apply()
        lc.action_issue()
        return lc

    # ── Amount and tolerance ────────────────────────────────────────────────
    def test_sequence_and_defaults(self):
        lc = self._lc()
        self.assertTrue(lc.name.startswith("LC/"))
        self.assertEqual(lc.state, "draft")

    def test_tolerance_raises_the_drawable_amount(self):
        lc = self._lc(tolerance_percent=10.0)
        self.assertAlmostEqual(lc.amount_max, 110000.0, places=2)
        self.assertAlmostEqual(lc.amount_available, 110000.0, places=2)
        self.assertEqual(lc.utilisation_status, "none")

    # ── Document control ────────────────────────────────────────────────────
    def test_documents_incomplete_until_all_received(self):
        lc = self._lc()
        self.assertEqual(lc.documents_required_count, 2)
        self.assertFalse(lc.documents_complete)
        lc.document_ids[0].write({"reference": "INV-1", "is_received": True})
        self.assertFalse(lc.documents_complete)
        lc.document_ids[1].write({"reference": "BL-1", "is_received": True})
        self.assertTrue(lc.documents_complete)
        self.assertEqual(lc.documents_received_count, 2)

    def test_credit_with_no_documents_is_not_complete(self):
        # Unconfigured must not read as unconditional.
        lc = self._lc(document_ids=[])
        self.assertFalse(lc.documents_complete)

    def test_receiving_a_document_requires_a_reference(self):
        lc = self._lc()
        with self.assertRaises(ValidationError):
            lc.document_ids[0].is_received = True

    def test_optional_document_does_not_hold_the_set(self):
        lc = self._lc()
        lc.document_ids[1].is_required = False
        lc.document_ids[0].write({"reference": "INV-1", "is_received": True})
        self.assertTrue(lc.documents_complete)

    def test_cannot_apply_without_listing_documents(self):
        lc = self._lc(document_ids=[])
        with self.assertRaises(UserError):
            lc.action_apply()

    def test_cannot_issue_without_bank_reference(self):
        lc = self._lc(lc_reference=False)
        lc.action_apply()
        with self.assertRaises(UserError):
            lc.action_issue()

    # ── Payment gating: the point of the module ─────────────────────────────
    def _posted_bill(self, lc=None):
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.supplier.id,
                "invoice_date": self.today,
                "odin_lc_id": lc.id if lc else False,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Inverters",
                            "quantity": 1,
                            "price_unit": 50000.0,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
        )
        bill.action_post()
        return bill

    def _register_payment(self, bill):
        return (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=bill.ids)
            .create({})
        )

    def test_payment_is_held_while_documents_are_outstanding(self):
        lc = self._issue(self._lc())
        bill = self._posted_bill(lc)
        self.assertTrue(bill.odin_lc_payment_blocked)
        self.assertEqual(bill.odin_lc_documents_outstanding, 2)
        wizard = self._register_payment(bill)
        with self.assertRaises(UserError):
            wizard._create_payments()

    def test_payment_released_once_documents_complete(self):
        lc = self._issue(self._lc())
        bill = self._posted_bill(lc)
        lc.document_ids[0].write({"reference": "INV-1", "is_received": True})
        lc.document_ids[1].write({"reference": "BL-1", "is_received": True})
        self.assertFalse(bill.odin_lc_payment_blocked)
        payments = self._register_payment(bill)._create_payments()
        self.assertTrue(payments)

    def test_a_bill_with_no_credit_is_never_held(self):
        bill = self._posted_bill()
        self.assertFalse(bill.odin_lc_payment_blocked)
        self.assertTrue(self._register_payment(bill)._create_payments())

    def test_purchase_order_carries_the_credit_onto_the_bill(self):
        lc = self._issue(self._lc())
        po = self.env["purchase.order"].create(
            {
                "partner_id": self.supplier.id,
                "odin_lc_id": lc.id,
                "order_line": [
                    Command.create(
                        {
                            "product_id": self.product_a.id,
                            "product_qty": 1,
                            "price_unit": 1000.0,
                            "name": "Inverter",
                            "date_planned": fields.Datetime.now(),
                        }
                    )
                ],
            }
        )
        self.assertEqual(po._prepare_invoice().get("odin_lc_id"), lc.id)

    # ── Utilisations ────────────────────────────────────────────────────────
    def _utilisation(self, lc, **overrides):
        vals = {
            "lc_id": lc.id,
            "amount": 40000.0,
            "date_presentation": self.today,
        }
        vals.update(overrides)
        return self.env["odin.lc.utilisation"].create(vals)

    def test_utilisation_reduces_availability(self):
        lc = self._issue(self._lc())
        util = self._utilisation(lc)
        util.action_present()
        util.action_accept()
        self.assertAlmostEqual(lc.amount_utilised, 40000.0, places=2)
        self.assertAlmostEqual(lc.amount_available, 60000.0, places=2)
        self.assertEqual(lc.utilisation_status, "partial")

    def test_cannot_draw_more_than_the_credit(self):
        lc = self._issue(self._lc())
        util = self._utilisation(lc, amount=150000.0)
        util.action_present()
        with self.assertRaises(ValidationError):
            util.action_accept()

    def test_presentation_after_expiry_is_refused(self):
        lc = self._issue(self._lc(date_expiry=self.today + timedelta(days=5)))
        util = self._utilisation(lc, date_presentation=self.today + timedelta(days=10))
        with self.assertRaises(UserError):
            util.action_present()

    def test_late_presentation_after_shipment_is_refused(self):
        lc = self._issue(self._lc(presentation_days=21))
        util = self._utilisation(
            lc,
            date_shipment=self.today - timedelta(days=40),
            date_presentation=self.today,
        )
        with self.assertRaises(UserError):
            util.action_present()

    def test_shipment_after_latest_shipment_date_is_a_discrepancy(self):
        lc = self._issue(
            self._lc(date_latest_shipment=self.today - timedelta(days=10))
        )
        with self.assertRaises(ValidationError):
            self._utilisation(lc, date_shipment=self.today)

    def test_discrepancy_must_be_itemised(self):
        lc = self._issue(self._lc())
        util = self._utilisation(lc)
        util.action_present()
        with self.assertRaises(UserError):
            util.action_mark_discrepant()
        util.discrepancy_note = "Late presentation; B/L not marked freight prepaid."
        util.action_mark_discrepant()
        self.assertEqual(util.state, "discrepant")
        # A waived discrepancy still lets the drawing be taken up.
        util.action_accept()
        self.assertEqual(util.state, "accepted")

    def test_usance_maturity_runs_from_shipment(self):
        lc = self._issue(self._lc(lc_type="usance", tenor_days=90))
        util = self._utilisation(lc, date_shipment=self.today)
        self.assertEqual(util.date_maturity, self.today + timedelta(days=90))

    def test_cannot_pay_before_documents_are_accepted(self):
        lc = self._issue(self._lc())
        util = self._utilisation(lc)
        util.action_present()
        with self.assertRaises(UserError):
            util.action_mark_paid()

    # ── Amendments ──────────────────────────────────────────────────────────
    def test_accepted_expiry_amendment_writes_through(self):
        lc = self._issue(self._lc())
        new_date = self.today + timedelta(days=180)
        amendment = self.env["odin.lc.amendment"].create(
            {
                "lc_id": lc.id,
                "amendment_type": "expiry",
                "description": "Extend expiry by 90 days",
                "date_new": new_date,
            }
        )
        amendment.action_request()
        self.assertEqual(amendment.date_old, self.today + timedelta(days=90))
        amendment.action_accept()
        self.assertEqual(lc.date_expiry, new_date)

    def test_requested_amendment_changes_nothing(self):
        lc = self._issue(self._lc())
        original = lc.amount
        amendment = self.env["odin.lc.amendment"].create(
            {
                "lc_id": lc.id,
                "amendment_type": "amount",
                "description": "Increase",
                "amount_new": 150000.0,
            }
        )
        amendment.action_request()
        self.assertAlmostEqual(lc.amount, original, places=2)
        amendment.action_accept()
        self.assertAlmostEqual(lc.amount, 150000.0, places=2)

    # ── Dates and lifecycle ─────────────────────────────────────────────────
    def test_shipment_date_after_expiry_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._lc(
                date_expiry=self.today + timedelta(days=30),
                date_latest_shipment=self.today + timedelta(days=60),
            )

    def test_usance_needs_a_tenor(self):
        with self.assertRaises(ValidationError):
            self._lc(lc_type="usance", tenor_days=0)

    def test_expiry_cron_expires_and_warns(self):
        # Issued normally, then aged. A credit cannot be ISSUED already expired —
        # the date constraint refuses that, correctly — so the test has to age a
        # live credit rather than create a dead one.
        lc = self._issue(self._lc())
        lc.write(
            {
                "date_issue": self.today - timedelta(days=30),
                "date_expiry": self.today - timedelta(days=1),
            }
        )
        self.env["odin.letter.of.credit"]._cron_expiry_watch()
        self.assertEqual(lc.state, "expired")

    def test_expiry_status_buckets(self):
        soon = self._issue(self._lc(date_expiry=self.today + timedelta(days=10)))
        self.assertEqual(soon.expiry_status, "soon")
        far = self._issue(
            self._lc(date_expiry=self.today + timedelta(days=200), lc_reference="ILC-2")
        )
        self.assertEqual(far.expiry_status, "open")

    def test_cannot_cancel_a_drawn_credit(self):
        lc = self._issue(self._lc())
        util = self._utilisation(lc)
        util.action_present()
        util.action_accept()
        util.action_mark_paid()
        with self.assertRaises(UserError):
            lc.action_cancel()

    def test_cannot_close_with_an_open_presentation(self):
        lc = self._issue(self._lc())
        util = self._utilisation(lc)
        util.action_present()
        with self.assertRaises(UserError):
            lc.action_close()

    def test_project_exposure_is_undrawn_import_availability(self):
        lc = self._issue(self._lc())
        self.assertEqual(self.project.odin_lc_count, 1)
        self.assertAlmostEqual(self.project.odin_lc_exposure, 100000.0, places=2)
        util = self._utilisation(lc, amount=25000.0)
        util.action_present()
        util.action_accept()
        self.assertAlmostEqual(self.project.odin_lc_exposure, 75000.0, places=2)
