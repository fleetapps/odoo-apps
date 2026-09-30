# Vendored — not our code

This directory is an unmodified copy of the OCA `purchase_request` module.

| | |
|---|---|
| Upstream | <https://github.com/OCA/purchase-workflow> |
| Branch | `19.0` |
| Commit | `c42eecd6fb4c9c50080e9e6ff22a7a2f687b48fd` |
| Module version | 19.0.1.0.2 |
| Licence | LGPL-3 (ForgeFlow, Odoo Community Association) |
| Vendored on | 2026-09-25 |

## Why it is here

`fms_connector` depends on it, and the sandbox addons path is the stock
`odoo:19.0` image plus `/opt/custom_addons` (this repo, as a submodule of
`fleetapps/odoo3`). OCA is on neither, so Odoo refused the install with an
unmet dependency. Vendoring the one module it needs was chosen over adding a
second submodule for the whole `purchase-workflow` repo, because the odoo3
Dockerfile copies exactly one `custom_addons` path and guards it.

## Rules

- **Do not edit anything in this directory.** Everything in it except this
  file is byte-identical to the upstream commit above, so a diff against
  upstream stays meaningful. Customisations belong in `fms_connector`, which
  extends `purchase.request` by inheritance.
- To update, re-copy from upstream at a newer commit and update the table
  above — then re-check the field names `fms_connector` relies on
  (`requested_by`, `assigned_to`, `picking_type_id`, `state` values,
  `line_ids`; and on the line: `product_id`, `name`, `product_qty`,
  `estimated_cost`, `date_required`, `purchase_lines`) plus the two inherited
  view ids (`view_purchase_request_form`, `view_purchase_request_search`).
  See `fms_connector/docs/DOCS_REGISTER.md`.
- LGPL-3 sits alongside the OPL-1 modules in this repo without infecting
  them: they are separate Odoo modules, and `fms_connector` depends on this
  one the same way Odoo's own Enterprise modules depend on Community ones.
