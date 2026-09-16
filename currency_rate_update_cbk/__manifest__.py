# Copyright 2026 Fleet Apps
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
#
# AGPL-3, not OPL-1, and deliberately so: this module inherits
# `res.currency.rate.provider` from OCA's `currency_rate_update`, which is
# AGPL-3. An OPL-1 module may not depend on an AGPL one, so this cannot be
# licensed like the rest of the ODIN apps in this repository. Keep it AGPL-3
# and keep its `depends` free of any OPL-1 module.
{
    "name": "Currency Rate Update - Central Bank of Kenya",
    "version": "19.0.1.0.0",
    "author": "Fleet Apps, Odin",
    "website": "https://github.com/fleetapps/odoo-apps",
    "license": "AGPL-3",
    "category": "Financial Management/Configuration",
    "summary": "Update KES exchange rates from the Central Bank of Kenya",
    "depends": ["currency_rate_update"],
    "installable": True,
}
