# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import date

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestOdinLd(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.partner = cls.env["res.partner"].create({"name": "Employer Ltd"})
        cls.project = cls.env["project.project"].create({"name": "Solar Plant A"})
        cls.account = cls.env["account.account"].create(
            {
                "name": "Liquidated Damages",
                "code": "LDTEST1",
                "account_type": "expense",
                "company_ids": [Command.link(cls.company.id)],
            }
        )
        cls.base_vals = {
            "project_id": cls.project.id,
            "partner_id": cls.partner.id,
            "direction": "payable",
            "contract_value": 100000.0,
            "basis": "per_day",
            "rate_amount": 1000.0,
            "cap_percent": 0.0,
            "date_contractual_completion": date(2026, 1, 1),
            "date_actual_completion": date(2026, 1, 11),
            "account_id": cls.account.id,
            "currency_id": cls.currency.id,
        }

    def _agreement(self, **overrides):
        vals = dict(self.base_vals, **overrides)
        agreement = self.env["odin.ld.agreement"].create(vals)
        agreement.action_activate()
        return agreement

    # ── Accrual arithmetic ──────────────────────────────────────────────────
    def test_sequence_assigned(self):
        agreement = self._agreement()
        self.assertNotEqual(agreement.name, "New")
        self.assertTrue(agreement.name.startswith("LDA/"))

    def test_accrual_per_day(self):
        agreement = self._agreement()
        self.assertEqual(agreement.days_delayed, 10)
        self.assertAlmostEqual(agreement.amount_accrued, 10000.0, places=2)
        self.assertFalse(agreement.is_capped)

    def test_cap_is_applied(self):
        # 10 days at 1,000 is 10,000, but the clause caps at 5% of 100,000.
        agreement = self._agreement(cap_percent=5.0)
        self.assertAlmostEqual(agreement.cap_amount, 5000.0, places=2)
        self.assertAlmostEqual(agreement.amount_accrued, 5000.0, places=2)
        self.assertTrue(agreement.is_capped)

    def test_grace_days_delay_the_start(self):
        agreement = self._agreement(grace_days=4)
        self.assertEqual(agreement.date_adjusted_completion, date(2026, 1, 5))
        self.assertEqual(agreement.days_delayed, 6)
        self.assertAlmostEqual(agreement.amount_accrued, 6000.0, places=2)

    def test_per_week_counts_whole_weeks_only(self):
        # 13 days of delay is one completed week, not two.
        agreement = self._agreement(
            basis="per_week",
            rate_amount=7000.0,
            date_actual_completion=date(2026, 1, 14),
        )
        self.assertEqual(agreement.days_delayed, 13)
        self.assertAlmostEqual(agreement.amount_accrued, 7000.0, places=2)

    def test_percent_per_day(self):
        agreement = self._agreement(
            basis="percent_per_day", rate_amount=0.0, rate_percent=0.5
        )
        # 10 days x 0.5% x 100,000
        self.assertAlmostEqual(agreement.amount_accrued, 5000.0, places=2)

    def test_no_damages_when_on_time(self):
        agreement = self._agreement(date_actual_completion=date(2026, 1, 1))
        self.assertEqual(agreement.days_delayed, 0)
        self.assertAlmostEqual(agreement.amount_accrued, 0.0, places=2)

    # ── Extensions of time ──────────────────────────────────────────────────
    def test_approved_eot_moves_completion(self):
        agreement = self._agreement()
        eot = self.env["odin.ld.eot"].create(
            {
                "agreement_id": agreement.id,
                "description": "Employer-caused delay to grid connection",
                "days_requested": 6,
                "date_claim": date(2026, 1, 2),
            }
        )
        # A claim that is only submitted must not move anything yet.
        eot.action_submit()
        self.assertEqual(agreement.days_delayed, 10)
        eot.action_approve()
        self.assertEqual(agreement.eot_days_granted, 6)
        self.assertEqual(agreement.date_adjusted_completion, date(2026, 1, 7))
        self.assertEqual(agreement.days_delayed, 4)
        self.assertAlmostEqual(agreement.amount_accrued, 4000.0, places=2)

    def test_rejected_eot_grants_nothing(self):
        agreement = self._agreement()
        eot = self.env["odin.ld.eot"].create(
            {
                "agreement_id": agreement.id,
                "description": "Weather",
                "days_requested": 6,
            }
        )
        eot.action_submit()
        eot.action_reject()
        self.assertEqual(eot.days_granted, 0)
        self.assertEqual(agreement.days_delayed, 10)

    def test_cannot_grant_more_days_than_claimed(self):
        agreement = self._agreement()
        eot = self.env["odin.ld.eot"].create(
            {
                "agreement_id": agreement.id,
                "description": "Access",
                "days_requested": 3,
            }
        )
        eot.action_submit()
        with self.assertRaises(ValidationError):
            eot.write({"state": "approved", "days_granted": 10})

    # ── Charges ─────────────────────────────────────────────────────────────
    def test_charge_net_of_waiver(self):
        agreement = self._agreement()
        charge = self.env["odin.ld.charge"].create(
            {
                "agreement_id": agreement.id,
                "date_from": date(2026, 1, 1),
                "date_to": date(2026, 1, 11),
                "days_charged": 10,
                "amount": 10000.0,
                "amount_waived": 2500.0,
            }
        )
        self.assertAlmostEqual(charge.amount_net, 7500.0, places=2)

    def test_cannot_waive_more_than_assessed(self):
        agreement = self._agreement()
        with self.assertRaises(ValidationError):
            self.env["odin.ld.charge"].create(
                {
                    "agreement_id": agreement.id,
                    "date_from": date(2026, 1, 1),
                    "date_to": date(2026, 1, 11),
                    "amount": 1000.0,
                    "amount_waived": 5000.0,
                }
            )

    def test_charge_cannot_exceed_cap(self):
        agreement = self._agreement(cap_percent=5.0)
        first = self.env["odin.ld.charge"].create(
            {
                "agreement_id": agreement.id,
                "date_from": date(2026, 1, 1),
                "date_to": date(2026, 1, 6),
                "amount": 5000.0,
            }
        )
        first.action_assess()
        first.action_approve()
        first.write({"state": "applied"})
        with self.assertRaises(ValidationError):
            self.env["odin.ld.charge"].create(
                {
                    "agreement_id": agreement.id,
                    "date_from": date(2026, 1, 6),
                    "date_to": date(2026, 1, 11),
                    "amount": 1000.0,
                    "state": "approved",
                }
            )

    def test_waiver_requires_a_reason(self):
        agreement = self._agreement()
        charge = self.env["odin.ld.charge"].create(
            {
                "agreement_id": agreement.id,
                "date_from": date(2026, 1, 1),
                "date_to": date(2026, 1, 11),
                "amount": 1000.0,
            }
        )
        with self.assertRaises(UserError):
            charge.action_waive()
        charge.waiver_reason = "Settled as part of the final account."
        charge.action_waive()
        self.assertEqual(charge.state, "waived")
        self.assertAlmostEqual(charge.amount_net, 0.0, places=2)

    # ── Direction ───────────────────────────────────────────────────────────
    def test_payable_agreement_rejects_purchase_order(self):
        po = self.env["purchase.order"].create({"partner_id": self.partner.id})
        with self.assertRaises(ValidationError):
            self._agreement(purchase_order_id=po.id)

    def test_early_completion_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._agreement(date_actual_completion=date(2025, 12, 1))

    # ── Deducting on a document ─────────────────────────────────────────────
    def _approved_charge(self, agreement, amount=10000.0):
        charge = self.env["odin.ld.charge"].create(
            {
                "agreement_id": agreement.id,
                "date_from": date(2026, 1, 1),
                "date_to": date(2026, 1, 11),
                "days_charged": 10,
                "amount": amount,
            }
        )
        charge.action_assess()
        charge.action_approve()
        return charge

    def _draft_invoice(self):
        return self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "invoice_date": date(2026, 1, 31),
                "currency_id": self.currency.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Works to 31 January",
                            "quantity": 1,
                            "price_unit": 50000.0,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
        )

    def test_apply_adds_a_negative_line_and_links_back(self):
        agreement = self._agreement()
        charge = self._approved_charge(agreement)
        invoice = self._draft_invoice()
        total_before = invoice.amount_total

        wizard = self.env["odin.ld.apply.wizard"].create(
            {
                "charge_id": charge.id,
                "move_id": invoice.id,
                "amount": 10000.0,
                "account_id": self.account.id,
                "label": "Liquidated damages",
            }
        )
        wizard.action_apply()

        self.assertEqual(charge.state, "applied")
        self.assertEqual(charge.move_id, invoice)
        self.assertTrue(charge.move_line_id)
        self.assertAlmostEqual(charge.move_line_id.price_unit, -10000.0, places=2)
        self.assertAlmostEqual(invoice.amount_total, total_before - 10000.0, places=2)
        self.assertEqual(invoice.odin_ld_charge_count, 1)
        self.assertAlmostEqual(invoice.odin_ld_amount, 10000.0, places=2)
        self.assertAlmostEqual(agreement.amount_charged, 10000.0, places=2)
        self.assertAlmostEqual(agreement.amount_open, 0.0, places=2)

    def test_cannot_deduct_more_than_the_net_charge(self):
        agreement = self._agreement()
        charge = self._approved_charge(agreement)
        invoice = self._draft_invoice()
        wizard = self.env["odin.ld.apply.wizard"].create(
            {
                "charge_id": charge.id,
                "move_id": invoice.id,
                "amount": 12000.0,
                "account_id": self.account.id,
                "label": "Too much",
            }
        )
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_cannot_deduct_a_payable_charge_on_a_vendor_bill(self):
        agreement = self._agreement()
        charge = self._approved_charge(agreement)
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.partner.id,
                "invoice_date": date(2026, 1, 31),
                "currency_id": self.currency.id,
            }
        )
        wizard = self.env["odin.ld.apply.wizard"].create(
            {
                "charge_id": charge.id,
                "move_id": bill.id,
                "amount": 1000.0,
                "account_id": self.account.id,
                "label": "Wrong document",
            }
        )
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_detach_removes_the_line(self):
        agreement = self._agreement()
        charge = self._approved_charge(agreement)
        invoice = self._draft_invoice()
        self.env["odin.ld.apply.wizard"].create(
            {
                "charge_id": charge.id,
                "move_id": invoice.id,
                "amount": 10000.0,
                "account_id": self.account.id,
                "label": "Liquidated damages",
            }
        ).action_apply()
        charge.action_detach()
        self.assertEqual(charge.state, "approved")
        self.assertFalse(charge.move_id)
        self.assertEqual(invoice.odin_ld_charge_count, 0)

    # ── Lifecycle ───────────────────────────────────────────────────────────
    def test_cannot_close_with_open_charges(self):
        agreement = self._agreement()
        self._approved_charge(agreement)
        with self.assertRaises(UserError):
            agreement.action_close()

    def test_cannot_cancel_once_applied(self):
        agreement = self._agreement()
        charge = self._approved_charge(agreement)
        invoice = self._draft_invoice()
        self.env["odin.ld.apply.wizard"].create(
            {
                "charge_id": charge.id,
                "move_id": invoice.id,
                "amount": 10000.0,
                "account_id": self.account.id,
                "label": "Liquidated damages",
            }
        ).action_apply()
        with self.assertRaises(UserError):
            agreement.action_cancel()

    def test_complete_requires_actual_completion(self):
        agreement = self._agreement(date_actual_completion=False)
        with self.assertRaises(UserError):
            agreement.action_complete()

    def test_project_exposure(self):
        agreement = self._agreement()
        self.assertEqual(self.project.odin_ld_agreement_count, 1)
        self.assertAlmostEqual(self.project.odin_ld_exposure, 10000.0, places=2)
        # Damages we recover from a subcontractor are a different counterparty's
        # liability and must not net off our own exposure.
        self._agreement(
            direction="receivable",
            partner_id=self.env["res.partner"].create({"name": "Sub Ltd"}).id,
        )
        self.assertAlmostEqual(self.project.odin_ld_exposure, 10000.0, places=2)
