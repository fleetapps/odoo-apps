# Copyright 2026 Fleet Apps
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

import calendar
import csv
import io
import logging
import re
from datetime import date

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TIMEOUT = 30

# The page that lists the published series. Each row is an <a href> to a CSV,
# and the numeric prefix in the filename changes whenever CBK re-uploads, so
# the URL is discovered rather than constructed. PINNED_URLS is the fallback
# for when the listing endpoint is unavailable.
CBK_LISTING_URL = (
    "https://www.centralbank.go.ke/wp-admin/admin-ajax.php"
    "?action=get_wdtable&table_id=167"
)

PINNED_URLS = {
    "end_period": (
        "https://www.centralbank.go.ke/uploads/exchange_rates/"
        "1863234131_Monthly exchange rate (end period).csv"
    ),
    "average": (
        "https://www.centralbank.go.ke/uploads/exchange_rates/"
        "656862271_Monthly Exchange rate (period average).csv"
    ),
}

# Substring that identifies each series in the listing's link text.
SERIES_MARKER = {"end_period": "END PERIOD", "average": "PERIOD AVERAGE"}

# CBK column header -> (ISO 4217 code, units quoted, inverted)
#
# Read off the published header row verbatim, typos and all ("Swdish kroner",
# the double space in "Hong  kong dollar", the "\2" footnote marker). Matching
# is exact, so a header CBK corrects later fails loudly in _map_columns rather
# than silently dropping a currency.
#
# `units` is how many units of the foreign currency the quote covers: CBK
# quotes the yen per 100, everything else per 1.
#
# `inverted` marks the East African currencies, which CBK quotes the other way
# round -- units of that currency per 1 KES, not KES per unit. The daily table
# names them "KES/USHS", "KES/TSHS", "KES/RWF" and "KES/BIF", and the monthly
# header footnotes Uganda and Tanzania with "\2" but not Rwanda or Burundi.
# Verified numerically against August 2026 instead of trusting the footnote:
# 1 KES was ~28.8 UGX, ~20.4 TZS, ~11.4 RWF and ~23.1 BIF, so all four are
# quoted per shilling.
#
# Pre-euro legacy columns (Deutch Mark, French franc, Dutch guilder, Italian
# lira, Belgium franc, Austrian schilling, Finn marka, Spanish peseta) are
# deliberately absent: they are empty for every row since 1999, and offering a
# dead currency in the provider's picker is worse than not offering it.
CBK_COLUMNS = {
    "United States dollar": ("USD", 1, False),
    "Sterling pound": ("GBP", 1, False),
    "Euro": ("EUR", 1, False),
    "South Africa Rand": ("ZAR", 1, False),
    "Uganda shilling\\2": ("UGX", 1, True),
    "Tanzania shilling\\2": ("TZS", 1, True),
    "Rwanda Franc": ("RWF", 1, True),
    "Burundi Franc": ("BIF", 1, True),
    "AE Dirham": ("AED", 1, False),
    "Canadian dollar": ("CAD", 1, False),
    "Swiss franc": ("CHF", 1, False),
    "Japanese yen (100)": ("JPY", 100, False),
    "Swdish kroner": ("SEK", 1, False),
    "Norwegian kroner": ("NOK", 1, False),
    "Danish kroner": ("DKK", 1, False),
    "Indian rupee": ("INR", 1, False),
    "Hong  kong dollar": ("HKD", 1, False),
    "Singapore dollar": ("SGD", 1, False),
    "Saudi riyal": ("SAR", 1, False),
    "Chinese Yuan": ("CNY", 1, False),
    "Australian dollar": ("AUD", 1, False),
}


class ResCurrencyRateProviderCBK(models.Model):
    _inherit = "res.currency.rate.provider"

    service = fields.Selection(
        selection_add=[("CBK", "Central Bank of Kenya")],
        ondelete={"CBK": "set default"},
    )
    cbk_series = fields.Selection(
        string="CBK Series",
        selection=[
            ("end_period", "End of month"),
            ("average", "Monthly average"),
        ],
        default="end_period",
        help=(
            "Which published series to read. End of month is the rate observed "
            "on the last business day and is the usual basis for revaluing "
            "balances; monthly average smooths the month and suits businesses "
            "that price that way."
        ),
    )

    @api.model
    def _cbk_currency_codes(self):
        return sorted({iso for iso, _units, _inv in CBK_COLUMNS.values()})

    def _get_supported_currencies(self):
        self.ensure_one()
        if self.service != "CBK":
            return super()._get_supported_currencies()
        # KES itself is included so a Kenyan company can select its own
        # currency without the picker looking broken; _update skips it.
        return self._cbk_currency_codes() + ["KES"]

    def _cbk_resolve_url(self):
        """Find the CSV for the configured series, falling back to the pin."""
        self.ensure_one()
        series = self.cbk_series or "end_period"
        marker = SERIES_MARKER[series]
        try:
            response = requests.post(
                CBK_LISTING_URL,
                data={"draw": 1, "start": 0, "length": 25},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            for row in response.json().get("data") or []:
                cell = row[0] if row else ""
                if marker not in cell.upper():
                    continue
                href = re.search(r'href="([^"]+)"', cell)
                if href:
                    return requests.compat.urljoin(
                        "https://www.centralbank.go.ke", href.group(1)
                    )
        except (requests.RequestException, ValueError, IndexError):
            # Discovery is a convenience, not the contract -- the pinned URL
            # has served the same file for years. Log and carry on.
            _logger.info(
                "CBK: could not resolve the %s series from the listing, "
                "using the pinned URL",
                series,
                exc_info=True,
            )
        return PINNED_URLS[series]

    def _cbk_map_columns(self, header):
        """Map header positions to (iso, units, inverted), failing loudly."""
        mapping = {}
        for index, label in enumerate(header):
            spec = CBK_COLUMNS.get(label.strip())
            if spec:
                mapping[index] = spec
        missing = set(CBK_COLUMNS) - {
            label.strip() for label in header if label.strip() in CBK_COLUMNS
        }
        if missing:
            # A renamed column would otherwise drop that currency silently and
            # leave the rate at whatever was last written.
            raise UserError(
                _(
                    "The Central Bank of Kenya file no longer has these "
                    "columns: %(columns)s. The published header has changed "
                    "and currency_rate_update_cbk needs updating.",
                    columns=", ".join(sorted(missing)),
                )
            )
        return mapping

    @api.model
    def _cbk_kes_per_unit(self, value, units, inverted):
        """Normalise a CBK quote to Kenya shillings per one unit."""
        quote = float(value)
        if not quote:
            return None
        if inverted:
            # Quoted as units of the currency per 1 KES.
            return 1.0 / quote
        return quote / units

    def _obtain_rates(self, base_currency, currencies, date_from, date_to):
        self.ensure_one()
        if self.service != "CBK":
            return super()._obtain_rates(base_currency, currencies, date_from, date_to)

        supported = set(self._get_supported_currencies())
        if base_currency not in supported:
            raise UserError(
                _(
                    "The Central Bank of Kenya publishes rates against the "
                    "Kenya shilling only, so it cannot price a company whose "
                    "currency is %(currency)s.",
                    currency=base_currency,
                )
            )

        url = self._cbk_resolve_url()
        response = requests.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        # The file is Latin-1 in places (CBK exports from Excel) and carries a
        # BOM; decode leniently rather than failing a whole run on one byte.
        text = response.content.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        if len(rows) < 3:
            raise UserError(
                _("The Central Bank of Kenya returned no usable rate data.")
            )

        # Row 0 is the title, row 1 the header, the rest are Year, Month, rates.
        mapping = self._cbk_map_columns(rows[1])
        wanted = set(currencies) | {base_currency}

        content = {}
        for row in rows[2:]:
            if len(row) < 3 or not row[0].strip().isdigit():
                continue
            year, month = int(row[0]), int(row[1])
            # End-of-month quotes are stamped on the day they describe; a
            # monthly average is stamped on the first, so it applies across
            # the month it averages rather than after it.
            if self.cbk_series == "average":
                stamp = date(year, month, 1)
            else:
                stamp = date(year, month, calendar.monthrange(year, month)[1])
            if (date_from and stamp < date_from) or (date_to and stamp > date_to):
                continue

            kes_per_unit = {"KES": 1.0}
            for index, (iso, units, inverted) in mapping.items():
                if index >= len(row):
                    continue
                value = row[index].strip()
                if not value:
                    continue
                try:
                    normalised = self._cbk_kes_per_unit(value, units, inverted)
                except ValueError:
                    continue
                if normalised:
                    kes_per_unit[iso] = normalised

            base = kes_per_unit.get(base_currency)
            if not base:
                continue
            rates = {
                iso: base / per_unit
                for iso, per_unit in kes_per_unit.items()
                if iso in wanted and iso != base_currency
            }
            if rates:
                content[stamp.isoformat()] = rates

        return content
