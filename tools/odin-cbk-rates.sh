#!/usr/bin/env bash
# Set up the Central Bank of Kenya exchange-rate feed on an Odin instance and
# fetch the latest rates. Run as root on the instance, once
# currency_rate_update_cbk is installed:
#
#   curl -fsSL https://raw.githubusercontent.com/fleetapps/odoo-apps/19.0/tools/odin-cbk-rates.sh | bash
#
# CURRENCIES (default USD) and SERIES (average, the default, or end_period)
# choose what is fetched. It makes or updates one CBK provider for the main
# company, checks it daily, turns on automatic rates, fetches the last three
# months (CBK publishes monthly; the average is dated the 1st of its month)
# and prints the newest rate of each currency. Odoo keeps running. Safe to
# re-run: that is also how to fetch again by hand.
set -euo pipefail

CURRENCIES="${CURRENCIES:-USD}"
SERIES="${SERIES:-average}"
case "$SERIES" in average|end_period) ;; *) echo "SERIES is average or end_period"; exit 1 ;; esac

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
[ -f /etc/odin/instance.json ] || { echo "/etc/odin/instance.json missing: is this an Odin instance?"; exit 1; }
DB="$(python3 -c "import json; print(json.load(open('/etc/odin/instance.json'))['db_name'])")"
. /etc/odin/odoo.env
CONF=/etc/odoo/odoo.conf
state="$(sudo -u postgres psql -tAc "SELECT state FROM ir_module_module WHERE name='currency_rate_update_cbk'" "$DB" 2>/dev/null || true)"
[ "$state" = installed ] || { echo "currency_rate_update_cbk is not installed in $DB"; exit 1; }

echo "== $DB: CBK $SERIES rates for $CURRENCIES"
docker run -i --rm --network host -v "$CONF:$CONF:ro" -v /mnt/extra-addons:/mnt/extra-addons:ro \
  -v /var/lib/odoo:/var/lib/odoo -v /opt/odin/pydeps:/opt/odin/pydeps:ro -e PYTHONPATH=/opt/odin/pydeps \
  -e CBK_CURRENCIES="$CURRENCIES" -e CBK_SERIES="$SERIES" \
  "$ODOO_IMAGE" odoo shell -c "$CONF" -d "$DB" --no-http --log-level=warn <<'PY'
import datetime, os
from dateutil.relativedelta import relativedelta

codes = [c.strip().upper() for c in os.environ["CBK_CURRENCIES"].split(",") if c.strip()]
company = env["res.company"].search([("parent_id", "=", False)], order="id", limit=1)
Currency = env["res.currency"].with_context(active_test=False)
currencies = Currency.search([("name", "in", codes)])
unknown = set(codes) - set(currencies.mapped("name"))
if unknown:
    raise SystemExit(f"unknown currencies: {', '.join(sorted(unknown))}")
currencies.write({"active": True})

Provider = env["res.currency.rate.provider"]
values = {"service": "CBK", "currency_ids": [(6, 0, currencies.ids)], "company_id": company.id,
          "cbk_series": os.environ["CBK_SERIES"]}
for key, value in (("interval_type", "days"), ("interval_number", 1)):
    if key in Provider._fields:
        values[key] = value
provider = Provider.search([("service", "=", "CBK"), ("company_id", "=", company.id)], limit=1)
if provider:
    provider.write(values)
else:
    provider = Provider.create(values)
if "currency_rates_autoupdate" in company._fields:
    company.currency_rates_autoupdate = True

today = datetime.date.today()
provider._update(today.replace(day=1) - relativedelta(months=3), today)
env.cr.commit()

Rate = env["res.currency.rate"]
for currency in currencies:
    rate = Rate.search([("currency_id", "=", currency.id), ("company_id", "in", [company.id, False])],
                       order="name desc", limit=1)
    if rate:
        print(f"   {currency.name}: {rate.name}  1 {currency.name} = {rate.inverse_company_rate:,.4f} {company.currency_id.name}")
    else:
        print(f"   {currency.name}: no rate came back; see the provider in Invoicing > Configuration")
PY
