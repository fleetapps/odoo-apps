from odoo.tests import TransactionCase, tagged

#: NCBA IPF rate card, effective 4 March 2026 -- Category A facility fees by
#: number of instalments, one per premium band.
BANDS = [25000, 150000, 170000, 200000, 250000, 270000]
FEES = {
    4: [3500, 3585, 4150, 5047, 5836, 6398],
    6: [5000, 6000, 6938, 8437, 9750, 10727],
    10: [7000, 10906, 12600, 15336, 17712, 19416],
}
#: Category B, KES 300,000 and over: % of the premium for the whole plan.
RATES = {4: 1.84, 6: 3.08, 10: 5.59}


@tagged("post_install", "-at_install")
class TestInstalmentRateCard(TransactionCase):
    """The instalment plans BizSure quotes are the NCBA card, cell for cell."""

    def plan(self, months):
        return self.env.ref("odin_bizsure_quotes.instalment_%sm" % {4: 3, 6: 6, 10: 10}[months])

    def test_months(self):
        self.assertEqual([self.plan(n).months for n in (4, 6, 10)], [4, 6, 10])

    def test_category_a(self):
        for months, fees in FEES.items():
            plan = self.plan(months)
            for low, high, fee in zip(BANDS, BANDS[1:] + [300000], fees):
                for premium in (low, (low + high) / 2, high - 0.01):
                    with self.subTest(months=months, premium=premium):
                        self.assertEqual(plan._odin_financing(premium), fee)
                        schedule = plan.schedule(premium)
                        self.assertAlmostEqual(schedule["instalment"],
                                               round((premium + fee) / months, 2))

    def test_category_b(self):
        for months, rate in RATES.items():
            for premium in (300000, 361443.03, 1000000):
                with self.subTest(months=months, premium=premium):
                    self.assertAlmostEqual(self.plan(months)._odin_financing(premium),
                                           premium * rate / 100, places=6)

    def test_below_minimum(self):
        for months in (4, 6, 10):
            self.assertFalse(self.plan(months)._odin_available(24999.99))
            self.assertTrue(self.plan(months)._odin_available(25000))
