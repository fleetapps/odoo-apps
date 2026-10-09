# Copyright 2026 Fleet Apps
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

from datetime import date
from unittest import mock

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models.res_currency_rate_provider_CBK import CBK_COLUMNS

# Two months of the real published file, trimmed to the columns under test.
# Kept as a literal so the suite does not depend on CBK being reachable.
SAMPLE_CSV = (
    ",Kenya Shilling End Period Exchange Rates\\1,,,,,,,\n"
    "Year,Month,United States dollar,Sterling pound,Euro,South Africa Rand,"
    "Uganda shilling\\2,Tanzania shilling\\2,Rwanda Franc,Burundi Franc,"
    "AE Dirham,Deutch Mark,Canadian dollar,French franc,Swiss franc,"
    "Dutch guilder,Italian lira,Belgium franc,Japanese yen (100),"
    "Swdish kroner,Norwegian kroner,Danish kroner,Austrian schilling,"
    "Finn marka,Spanish peseta,Indian rupee,Hong  kong dollar,"
    "Singapore dollar,Saudi riyal,Chinese Yuan,Australian dollar\n"
    "2026,7,129.4,174.01,148.91,7.84,28.97,20.39,11.34,23.08,35.23,,92.31,,"
    "160.29,,,,80.55,13.54,13.55,19.92,,,,1.35,16.5,100.8,34.46,19.17,90.95\n"
    "2026,8,129.45,175.4,150.35,8.03,29.12,20.43,11.36,23.11,35.24,,93.26,,"
    "160.46,,,,81.04,13.53,13.83,20.11,,,,1.35,16.51,101.69,34.48,19.25,92.91\n"
)


class _Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


@tagged("post_install", "-at_install")
class TestCurrencyRateUpdateCBK(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env["res.currency.rate.provider"].create(
            {
                "service": "CBK",
                "currency_ids": [
                    (
                        6,
                        0,
                        cls.env["res.currency"]
                        .search([("name", "in", ["USD", "AED", "JPY", "UGX"])])
                        .ids,
                    )
                ],
            }
        )

    def _obtain(self, base="KES", currencies=None, series="end_period"):
        self.provider.cbk_series = series
        with mock.patch.object(
            type(self.provider), "_cbk_resolve_url", return_value="http://test"
        ), mock.patch(
            "odoo.addons.currency_rate_update_cbk.models."
            "res_currency_rate_provider_CBK.requests.get",
            return_value=_Response(SAMPLE_CSV.encode()),
        ):
            return self.provider._obtain_rates(
                base,
                currencies or ["USD", "AED", "JPY", "UGX"],
                date(2026, 8, 1),
                date(2026, 8, 31),
            )

    def test_supported_currencies_include_kes(self):
        self.assertIn("KES", self.provider._get_supported_currencies())
        self.assertIn("USD", self.provider._get_supported_currencies())

    def test_end_period_is_stamped_on_the_last_day(self):
        self.assertEqual(list(self._obtain()), ["2026-08-31"])

    def test_average_is_stamped_on_the_first_day(self):
        self.assertEqual(
            list(self._obtain(series="average")),
            ["2026-08-01"],
        )

    def test_direct_quote_is_inverted_for_a_kes_company(self):
        # CBK quotes 129.45 KES per USD, so 1 KES buys 1/129.45 USD.
        self.assertAlmostEqual(
            self._obtain()["2026-08-31"]["USD"], 1 / 129.45, places=9
        )

    def test_yen_is_quoted_per_hundred(self):
        # 81.04 KES per 100 JPY -> 0.8104 per yen -> 1/0.8104 yen per shilling.
        self.assertAlmostEqual(
            self._obtain()["2026-08-31"]["JPY"], 1 / (81.04 / 100), places=9
        )

    def test_east_african_currencies_are_already_inverted(self):
        # CBK publishes 29.12 UGX per KES, so it is used as-is, not inverted.
        self.assertAlmostEqual(
            self._obtain()["2026-08-31"]["UGX"], 29.12, places=9
        )

    def test_cross_rate_for_a_non_kes_company(self):
        rates = self._obtain(base="USD", currencies=["KES", "EUR"])
        self.assertAlmostEqual(rates["2026-08-31"]["KES"], 129.45, places=6)
        self.assertAlmostEqual(
            rates["2026-08-31"]["EUR"], 129.45 / 150.35, places=9
        )

    def test_unsupported_base_currency_is_refused(self):
        with self.assertRaises(UserError):
            self._obtain(base="BRL", currencies=["USD"])

    def test_renamed_column_fails_loudly(self):
        header = [label for label in CBK_COLUMNS]
        header[0] = "United States Dollar (renamed)"
        with self.assertRaises(UserError):
            self.provider._cbk_map_columns(header)
