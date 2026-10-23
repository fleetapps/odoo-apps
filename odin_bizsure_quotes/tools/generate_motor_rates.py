"""Bizsure's motor rate cards (MOTOR_RATES_REVAMPED_1.xlsx) as Odoo data."""
import sys
from xml.sax.saxutils import escape

INSURERS = {
    "definite": "Definite Assurance",
    "fidelity": "Fidelity Insurance",
    "cic": "CIC Insurance",
    "ga": "GA Insurance",
    "amaco": "Africa Merchant Assurance (AMACO)",
    "pioneer": "Pioneer Insurance",
}
C, T = "comprehensive", "tpo"
INC, OPT = "included", "optional"
R = []  # (insurer, class, cover, fields)


def r(insurer, cls, cover, **f):
    R.append((insurer, cls, cover, f))


# -- Definite: third party only (selling premiums)
r("definite", "private", T, flat_premium=4200)
r("definite", "private", T, flat_premium=1000, period_months=1)
for low, high, price in ((0, 3, 5000), (3.01, 8, 7000), (8.01, 12, 11500), (12.01, 20, 15000), (20.01, 0, 18000)):
    r("definite", "commercial", T, flat_premium=price, tonnage_min=low, tonnage_max=high)
r("definite", "commercial", T, flat_premium=1500, period_months=1, tonnage_max=3)
r("definite", "commercial", T, flat_premium=2000, period_months=1, tonnage_min=3.01, tonnage_max=8)
r("definite", "prime_mover", T, flat_premium=20000)
for low, high, price in ((0, 9, 7500), (10, 17, 10000), (18, 25, 14000), (26, 0, 16500)):
    r("definite", "psv", T, flat_premium=price, seats_min=low, seats_max=high, pll_amount=500, pll_required=True)
for low, high, price in ((0, 9, 9000), (10, 25, 14000), (26, 0, 16500)):
    r("definite", "tsv", T, flat_premium=price, seats_min=low, seats_max=high, pll_amount=500, pll_required=True)
r("definite", "motorcycle_psv", T, flat_premium=3700)
r("definite", "motorcycle_psv", T, flat_premium=2500, period_months=6)
r("definite", "motorcycle_psv", T, flat_premium=1000, period_months=1)
r("definite", "motorcycle_private", T, flat_premium=3500)
r("definite", "tuktuk_psv", T, flat_premium=5000, note="Includes passenger legal liability for 3 passengers.")
r("definite", "tuktuk_psv", T, flat_premium=1500, period_months=1)
r("definite", "tuktuk_commercial", T, flat_premium=4500)
r("definite", "special", T, flat_premium=10000)
r("definite", "construction", T, flat_premium=10000)
# -- Definite: comprehensive
r("definite", "private", C, value_min=500000, max_age=15, rate=3.5, min_premium=37500, pvt=INC,
  excess=OPT, excess_rate=0.25, excess_min=3000, courtesy_10=3000, courtesy_20=6000)
r("definite", "commercial", C, value_min=500000, max_age=15, rate=4.5, min_premium=40000, pvt=INC,
  excess=OPT, excess_rate=0.35, excess_min=5000)
r("definite", "institutional", C, value_min=500000, max_age=15, rate=3.25, min_premium=40000, pvt=INC,
  excess=OPT, excess_rate=0.25, excess_min=3000, pll_amount=250)
r("definite", "psv", C, value_min=500000, max_age=15, rate=5.5, min_premium=42500, pvt=OPT, pvt_amount=2500,
  pll_amount=500, pll_required=True)
r("definite", "tuktuk_commercial", C, value_min=250000, max_age=10, rate=4, min_premium=15000, pvt=OPT, pvt_amount=1500)
r("definite", "tuktuk_psv", C, value_min=250000, max_age=10, rate=4, min_premium=20000, pvt=OPT, pvt_amount=1500,
  pll_amount=500, pll_required=True)
r("definite", "tsv", C, value_min=750000, max_age=15, rate=4.5, min_premium=40000, pvt=INC,
  excess=OPT, excess_rate=0.5, excess_min=5000, pll_amount=500, pll_required=True)
r("definite", "motorcycle_psv", C, value_min=150000, max_age=5, rate=5, min_premium=9000, pvt=INC,
  pll_amount=500, pll_required=True)
r("definite", "motorcycle_private", C, value_min=150000, max_age=5, rate=3, min_premium=6000, pvt=INC)

# -- Fidelity: comprehensive
pvt_excess = dict(pvt=OPT, pvt_rate=0.25, pvt_min=3000, excess=OPT, excess_rate=0.25, excess_min=3000)
r("fidelity", "private", C, value_min=500000, value_max=999999, max_age=15, rate=6, min_premium=40000, **pvt_excess)
r("fidelity", "private", C, value_min=1000000, value_max=1499999, max_age=16, rate=5, **pvt_excess)
r("fidelity", "private", C, value_min=1500000, value_max=2499999, max_age=17, rate=4, **pvt_excess)
r("fidelity", "private", C, value_min=2500000, value_max=3499999, max_age=18, rate=3.25, **pvt_excess)
r("fidelity", "private", C, value_min=3500000, rate=3, pvt=INC, excess=OPT, excess_amount=5000,
  extra_label="Personal accident cover (mandatory)", extra_amount=2000)
fid = dict(pvt=INC, excess=OPT, excess_amount=3000)
r("fidelity", "institutional", C, rate=3.5, min_premium=40000, pll_amount=300, **fid)
r("fidelity", "pickup", C, rate=4.25, min_premium=40000, **fid)
r("fidelity", "commercial", C, rate=4.8, min_premium=50000, **fid)
r("fidelity", "farm", C, rate=2.5, min_premium=25000, **fid)
r("fidelity", "tsv", C, rate=4.5, min_premium=50000, **fid)
r("fidelity", "tuktuk_commercial", C, rate=3, min_premium=20000, **fid)
r("fidelity", "special", C, rate=5.75, min_premium=50000, **fid)

# -- CIC
cic = dict(pvt=INC, excess=OPT, excess_amount=2500)
r("cic", "private", C, value_min=500000, value_max=1000000, max_age=15, rate=5.5, min_premium=40000, **cic)
r("cic", "private", C, value_min=1000000, value_max=1500000, max_age=15, rate=4.5, min_premium=40000, **cic)
r("cic", "private", C, value_min=1500000, value_max=2500000, max_age=15, rate=4.25, min_premium=40000, **cic)
r("cic", "private", C, value_min=2500000, value_max=3000000, max_age=15, rate=3.5, min_premium=40000, **cic)
r("cic", "private", C, value_min=3000000, value_max=5000000, max_age=15, rate=3.25, pvt=INC, excess=OPT, excess_amount=5000)
r("cic", "private", C, value_min=5000000, max_age=15, rate=3, pvt=INC, excess=OPT, excess_amount=5000)
r("cic", "institutional", C, max_age=15, rate=3.25, min_premium=42500, pvt=INC, excess=INC, pll_amount=250)
r("cic", "motorcycle_commercial", C, rate=3, min_premium=6000)
r("cic", "motorcycle_commercial", T, flat_premium=4000)
for low, high, price in ((0, 14, 7500), (15, 25, 12500), (26, 0, 18000)):
    r("cic", "institutional", T, flat_premium=price, seats_min=low, seats_max=high, pll_amount=250)

# -- GA
r("ga", "private", C, value_min=1000000, value_max=3000000, max_age=15, rate=4, min_premium=42500,
  pvt=INC, excess=OPT, excess_amount=2500)
r("ga", "private", C, value_min=8000000, max_age=15, rate=3.25, min_premium=42500,
  pvt=INC, excess=OPT, excess_amount=2500)
r("ga", "private", T, flat_premium=12500)
r("ga", "commercial", C, value_min=1000000, max_age=15, rate=6, min_premium=50000, pvt=INC, excess=INC)
r("ga", "institutional", C, value_min=1000000, max_age=15, rate=5, min_premium=50000, pvt=INC, excess=INC,
  pll_amount=500)

# -- AMACO
r("amaco", "psv", C, max_age=15, rate=5, min_premium=42500, pvt=OPT, pvt_rate=0.45, pvt_min=2500,
  excess=OPT, excess_rate=1, excess_min=10000, pll_amount=250, pll_required=True)

# -- Pioneer
r("pioneer", "psv", C, value_min=500000, max_age=15, rate=6, min_premium=40000, pvt=OPT, pvt_amount=2500,
  pll_amount=500, pll_required=True)
r("pioneer", "private", C, value_min=500000, value_max=999999, max_age=15, rate=6, min_premium=37500,
  pvt=OPT, pvt_rate=0.25, pvt_min=2500, excess=OPT, excess_rate=0.25, excess_min=2500,
  courtesy_10=4500, courtesy_20=7500)
r("pioneer", "private", C, value_min=1000000, value_max=1499999, max_age=15, rate=5, pvt=INC, excess=OPT, excess_amount=2500)
r("pioneer", "private", C, value_min=1500000, value_max=2499999, max_age=15, rate=4, pvt=INC, excess=OPT, excess_amount=2500)
r("pioneer", "private", C, value_min=2500000, value_max=4999999, max_age=15, rate=3.25, pvt=INC, excess=OPT, excess_amount=5000)
r("pioneer", "private", C, value_min=5000000, max_age=15, rate=3, pvt=INC, excess=OPT, excess_amount=5000)
r("pioneer", "commercial", C, max_age=15, rate=4.5, min_premium=50000, pvt=INC, excess=OPT, excess_rate=0.25, excess_min=5000)
r("pioneer", "farm", C, rate=2.75, min_premium=40000, pvt=OPT, pvt_rate=0.25, pvt_min=2500,
  excess=OPT, excess_rate=0.5, excess_min=5000)
r("pioneer", "construction", C, rate=3, min_premium=40000, pvt=OPT, pvt_rate=0.25, pvt_min=2500,
  excess=OPT, excess_rate=0.5, excess_min=5000)
r("pioneer", "institutional", C, max_age=15, rate=3.5, pvt=INC, excess=INC, pll_amount=250)
r("pioneer", "motorcycle_commercial", C, rate=3, min_premium=6000)
r("pioneer", "tuktuk_commercial", C, rate=3, min_premium=12000)
r("pioneer", "tsv", C, max_age=15, rate=5, min_premium=42500, pvt=OPT, pvt_rate=0.25, pvt_min=2500,
  excess=OPT, excess_rate=0.5, excess_min=5000, pll_amount=500, pll_required=True)
r("pioneer", "private", T, flat_premium=8000)
for low, high, price in ((0, 3, 8000), (3.01, 8, 10500), (8.01, 15, 15500), (15.01, 0, 20500)):
    r("pioneer", "commercial", T, flat_premium=price, tonnage_min=low, tonnage_max=high)
r("pioneer", "farm", T, flat_premium=8000)
r("pioneer", "construction", T, flat_premium=10500)
r("pioneer", "motorcycle_commercial", T, flat_premium=4000)
r("pioneer", "tuktuk_commercial", T, flat_premium=6000)

out = ['<?xml version="1.0" encoding="utf-8"?>',
       '<!-- Bizsure\'s motor rate cards, from MOTOR_RATES_REVAMPED_1.xlsx (selling premiums).',
       '     Maintained in Cover Quotes > Configuration > Motor rates: noupdate, so an',
       '     upgrade does not undo the office\'s changes. Generated by',
       '     tools/generate_motor_rates.py. -->',
       '<odoo noupdate="1">']
for key, name in INSURERS.items():
    out += ['    <record id="insurer_%s" model="res.partner">' % key,
            '        <field name="name">%s</field>' % escape(name),
            '        <field name="is_company" eval="True"/>',
            '    </record>']
count = {}
for insurer, cls, cover, f in R:
    count[insurer] = count.get(insurer, 0) + 1
    out.append('    <record id="motor_rate_%s_%03d" model="odin.motor.rate">' % (insurer, count[insurer]))
    out.append('        <field name="insurer_id" ref="insurer_%s"/>' % insurer)
    out.append('        <field name="class_id" ref="odin_motor_quote.class_%s"/>' % cls)
    out.append('        <field name="cover">%s</field>' % cover)
    for field, value in f.items():
        if isinstance(value, bool):
            out.append('        <field name="%s" eval="%s"/>' % (field, value))
        else:
            out.append('        <field name="%s">%s</field>' % (field, escape(str(value))))
    out.append('    </record>')
out.append('</odoo>')
sys.stdout.write("\n".join(out) + "\n")
print("<!-- %s rates -->" % len(R), file=sys.stderr)
