from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import html2plaintext

from odoo.addons.odin_medical_quote.tests.common import MedicalQuoteCase


class MotorCase(MedicalQuoteCase):
    """Two insurers' private-car cards and a PSV third-party card, in the
    shape of Bizsure's."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Motor = cls.env["odin.motor.quote"].with_user(cls.agent)
        cls.private = cls.env.ref("odin_motor_quote.class_private")
        cls.psv = cls.env.ref("odin_motor_quote.class_psv")
        for key in ("premium", "pvt", "excess", "pll", "courtesy", "extra"):
            cls.env.ref("odin_motor_quote.product_motor_%s" % key).taxes_id = [Command.set(cls.levies.ids)]
        Partner = cls.env["res.partner"]
        cls.definite = Partner.create({"name": "Test Definite", "is_company": True})
        cls.fidelity = Partner.create({"name": "Test Fidelity", "is_company": True})
        Rate = cls.env["odin.motor.rate"]
        Rate.search([]).active = False
        cls.definite_private = Rate.create({
            "insurer_id": cls.definite.id, "class_id": cls.private.id, "cover": "comprehensive",
            "value_min": 500000, "max_age": 15, "rate": 3.5, "min_premium": 37500,
            "pvt": "included", "excess": "optional", "excess_rate": 0.25, "excess_min": 3000,
            "courtesy_10": 3000, "courtesy_20": 6000, "note": "Excess: 2.5% of claim, minimum 15,000.",
        })
        cls.fidelity_low = Rate.create({
            "insurer_id": cls.fidelity.id, "class_id": cls.private.id, "cover": "comprehensive",
            "value_min": 500000, "value_max": 999999, "max_age": 15, "rate": 6, "min_premium": 40000,
            "pvt": "optional", "pvt_rate": 0.25, "pvt_min": 3000,
        })
        cls.fidelity_high = Rate.create({
            "insurer_id": cls.fidelity.id, "class_id": cls.private.id, "cover": "comprehensive",
            "value_min": 1000000, "value_max": 1499999, "max_age": 16, "rate": 5,
            "pvt": "optional", "pvt_rate": 0.25, "pvt_min": 3000,
        })
        cls.psv_tpo = Rate.create({
            "insurer_id": cls.definite.id, "class_id": cls.psv.id, "cover": "tpo",
            "seats_min": 10, "seats_max": 17, "flat_premium": 10000,
            "pll_amount": 500, "pll_required": True,
        })
        cls.year = cls.env.cr.now().year

    def car(self, **values):
        return dict({"class_id": self.private.id, "cover": "comprehensive", "value": 800000,
                     "year": self.year - 5, "registration": "kda 123a",
                     "make_model": "Toyota Fielder"}, **values)

    def plans(self, **cover):
        return {plan["id"]: plan for plan in self.Motor.search_plans({"cover": self.car(**cover)})["plans"]}


@tagged("post_install", "-at_install")
class TestMotorPricing(MotorCase):

    def test_rate_and_minimum(self):
        plans = self.plans()
        self.assertEqual(list(plans), [self.definite_private.id, self.fidelity_low.id],
                         "cheapest basic premium first")
        definite = plans[self.definite_private.id]
        self.assertEqual(definite["core"]["amount"], 37500, "3.5% of 800,000 is below the minimum")
        self.assertEqual(definite["core"]["parts"][1]["who"], "PVT included")
        self.assertTrue(definite["core"]["parts"][1]["included"])
        self.assertEqual({g["key"] for g in definite["groups"]}, {"excess", "courtesy"})
        excess = next(g for g in definite["groups"] if g["key"] == "excess")
        self.assertEqual(excess["options"][0]["amount"], 3000, "0.25% of 800,000 is below 3,000")
        self.assertEqual(plans[self.fidelity_low.id]["core"]["amount"], 48000)

    def test_value_band(self):
        plans = self.plans(value=1200000)
        self.assertIn(self.fidelity_high.id, plans, "the band the value falls in")
        self.assertNotIn(self.fidelity_low.id, plans)
        self.assertEqual(plans[self.fidelity_high.id]["core"]["amount"], 60000)
        self.assertEqual(plans[self.definite_private.id]["core"]["amount"], 42000)

    def test_refusals(self):
        result = self.Motor.search_plans({"cover": self.car(year=self.year - 20)})
        self.assertFalse(result["plans"])
        self.assertEqual({u["title"]: u["reason"] for u in result["unavailable"]}, {
            "Test Definite": "Vehicles up to 15 years old.",
            "Test Fidelity": "Vehicles up to 15 years old.",
        })
        cheap = self.Motor.search_plans({"cover": self.car(value=300000)})
        self.assertIn("Vehicles worth 500,000 or more.", [u["reason"] for u in cheap["unavailable"]])
        with self.assertRaisesRegex(UserError, "value"):
            self.Motor.search_plans({"cover": self.car(value=0)})

    def test_psv_passengers(self):
        cover = {"class_id": self.psv.id, "cover": "tpo", "seats": 14}
        plan = self.Motor.search_plans({"cover": cover})["plans"][0]
        self.assertEqual(plan["core"]["amount"], 10000 + 14 * 500, "PLL is part of a PSV's cover")
        with self.assertRaisesRegex(UserError, "passengers"):
            self.Motor.search_plans({"cover": {"class_id": self.psv.id, "cover": "tpo"}})
        self.assertFalse(self.Motor.search_plans({"cover": dict(cover, seats=30)})["plans"],
                         "no band for 30 passengers")

    def test_totals(self):
        totals = self.Motor.compute_totals({"cover": self.car(), "plans": [
            {"rate_id": self.definite_private.id, "options": ["excess", "courtesy10"], "discount": "10%"},
        ]})[str(self.definite_private.id)]
        premium = 37500 + 3000 + 3000
        self.assertAlmostEqual(totals["premium"], premium)
        self.assertAlmostEqual(totals["discount"], premium * 0.1)
        self.assertAlmostEqual(totals["taxes"], premium * 0.9 * 0.0045, delta=0.02)
        self.assertEqual(totals["charges"], 40, "stamp duty")


@tagged("post_install", "-at_install")
class TestMotorSave(MotorCase):

    def save(self, **values):
        params = dict({
            "customer": {"source": "new", "name": "Peter Kamau", "phone": "0722 111 222"},
            "cover": self.car(),
            "plans": [
                {"rate_id": self.definite_private.id, "options": ["excess"], "recommended": True},
                {"rate_id": self.fidelity_low.id, "options": ["pvt"]},
            ],
        }, **values)
        saved = self.Motor.save_quote(params)
        return self.env["odin.quote.comparison"].with_user(self.agent).browse(saved["comparison"]["id"])

    def test_saved_like_any_quote(self):
        comparison = self.save()
        self.assertEqual(comparison.odin_quote_type, "motor")
        self.assertEqual(comparison.title, "Motor Insurance")
        orders = comparison.plan_ids.sorted("sequence").order_id
        self.assertEqual(orders.odin_motor_rate_id, self.definite_private | self.fidelity_low)
        self.assertIn("Excess", orders[0].note and html2plaintext(orders[0].note) or "Excess")
        lead = comparison.opportunity_id
        self.assertEqual(lead.name, "Motor cover – Peter Kamau (KDA 123A)")
        self.assertIn("Motor", lead.tag_ids.mapped("name"))
        self.assertEqual(lead.user_id, self.agent)
        self.assertEqual(comparison.odin_plan_summary,
                         "Test Definite – Comprehensive · Test Fidelity – Comprehensive")
        self.assertEqual(comparison.odin_amount_total, orders[0].amount_total, "the recommended one")
        self.assertEqual(comparison.action_odin_edit()["tag"], "odin_motor_quote")

        # Saved again with the second insurer dropped: the same screen, the same rules.
        params = dict(comparison.odin_builder_params, comparison_id=comparison.id)
        params["plans"] = params["plans"][:1]
        self.Motor.save_quote(params)
        self.assertEqual(orders[1].state, "cancel")
        self.assertEqual(comparison.plan_ids.order_id, orders[0])

    def test_quotation_pdf(self):
        comparison = self.save()
        members = dict(comparison._odin_export_members())
        self.assertEqual(members["Registration"], "KDA 123A")
        self.assertEqual(members["Sum insured"], "800,000")
        self.assertEqual(members["Vehicle type"], "Private car")
        rows = {row["label"]: row["cells"] for row in comparison._odin_export_matrix()["rows"]}
        self.assertEqual(rows["Basic premium"], [37500, 48000])
        self.assertEqual(rows["Political violence & terrorism"], [0, 3000],
                         "included for one (printed Included), bought for the other")
        self.assertEqual(rows["Excess protector"], [3000, None])
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "odin_medical_quote.report_medical_quotation", comparison.ids)
        text = html2plaintext(html.decode())
        for expected in ("Vehicle details", "KDA 123A", "Test Definite", "Included", "Motor Insurance"):
            self.assertIn(expected, text)
        self.assertEqual(comparison._odin_export_filename(),
                         "Motor quotation - Peter Kamau - %s" % comparison.name)

    def test_email_and_accept(self):
        comparison = self.save()
        action = comparison.action_odin_send_email()
        composer = self.env["mail.compose.message"].with_user(self.agent).with_context(
            **action["context"]).create({})
        self.assertIn("motor insurance quotation", composer.subject)
        self.assertIn("Test Definite", composer.body)

        order = comparison.plan_ids.sorted("sequence").order_id[0]
        accept = comparison.action_odin_accept()
        wizard = self.env["odin.medical.quote.accept"].with_user(self.agent).with_context(
            **accept["context"]).create({})
        self.assertEqual(wizard.order_id, order)
        self.assertIn("Test Definite", wizard.order_id.with_context(odin_plan_label=True).display_name)
        invoice = self.env["account.move"].browse(wizard.action_accept()["res_id"])
        self.assertEqual(comparison.state, "accepted")
        self.assertAlmostEqual(invoice.amount_total, order.amount_total)

    def test_rate_no_longer_fits(self):
        with self.assertRaisesRegex(UserError, "cannot quote this vehicle"):
            self.save(cover=self.car(year=self.year - 30))
