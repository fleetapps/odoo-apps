# -*- coding: utf-8 -*-
# Manifest reference:
# https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
#
# TARGET: Odoo 18/19 ONLY. Do not port this module to Odoo 20.
# It is a bespoke bridge for one customer who runs 18/19, not an App Store
# listing, and nobody who uses it is on 20 (confirmed with the customer,
# 2026-09-24). It therefore deliberately keeps the 19.0 security shapes --
# security/ir.model.access.csv and an ir.rule -- which Odoo 20 replaced with
# the unified ir.access model. The rest of this repo lives on the 20.0 branch
# and has been ported; this module is the standing exception.
{
    "name": "Fleet FMS Connector",
    "version": "19.0.1.0.0",
    "category": "Purchases/Purchases",
    "summary": "Bridges the Fleet FMS vehicle-maintenance workflow into Purchase "
               "Requests and Expenses: FMS pushes a purchase request when a "
               "driver raises one, Odoo pushes back the approved vendor/PO "
               "once bid analysis and approval are done in Odoo, and FMS "
               "pushes a draft expense once the job card and invoice are in.",
    "description": """
Fleet FMS Connector
====================

A narrow, two-touchpoint bridge between the Fleet FMS app (where drivers
raise requests and job cards live) and Odoo (where RFQs, bid analysis, PO
approval and expense approval already happen and are staying put).

* **FMS -> Odoo:** a driver's purchase request becomes a ``purchase.request``
  record here, with the requesting employee resolved by email, an
  approver assigned by branch/country routing, and enough reference fields
  (driver, vehicle, branch, country, FMS id) to trace it back. RFQs, vendor
  quotations, bid analysis and PO generation stay exactly where they are
  today, in Odoo's own Purchase app.
* **Odoo -> FMS:** once the resulting purchase order is confirmed, the
  connector calls back into FMS with the winning vendor and approved amount,
  so FMS can let the admin/driver proceed with the vehicle.
* **FMS -> Odoo:** once the vehicle is serviced and the vendor invoice is in
  hand, FMS pushes a draft ``hr.expense`` (employee, amount, vehicle
  reference, invoice attached) for approval inside Odoo -- no line-item
  labor hours or technical rates, just the total and who raised it.

FMS remains responsible for the approval-request emails it sends (they carry
a link straight into the Odoo record); this module never sends its own.

No phone-home, no CDN -- everything runs inside your Odoo.
""",
    "author": "Fleet",
    "website": "https://www.odin.ist",
    "support": "support@odin.ist,andrew@fleet.ke",
    "license": "OPL-1",
    "depends": [
        "purchase_request",
        "hr_expense",
        "mail",
    ],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/fms_connector_security.xml",
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/fms_branch_route_views.xml",
        "views/purchase_request_views.xml",
        "views/hr_expense_views.xml",
    ],
    "pre_init_hook": "pre_init_check",
    "installable": True,
    "application": False,
}
