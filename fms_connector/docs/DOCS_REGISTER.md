# VERIFY-ON-BUILD register — fms_connector

Every platform assumption baked into this module, closed with the document
consulted and the date. Re-verify anything touching `purchase_request`
against the customer's actual installed version before go-live -- it is an
OCA module they maintain, not one Odoo SA ships, so what's on their
addons path may not exactly match the 19.0 branch tip this was built
against.

| # | Item | Decision baked into code | Doc consulted | Date |
|---|---|---|---|---|
| 1 | External API choice | Standard XML-RPC/JSON-RPC (`execute_kw`, API-key auth) works unchanged on both 18.0 and 19.0 and is not removed until Odoo 22 (~fall 2028) / Online 21.1 -- but this module exposes its **own** JSON controllers instead, so FMS talks to a versioned, validated contract rather than raw model CRUD, and business logic (employee/approver resolution, required-field defaults, attachments) runs server-side | <https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html>, <https://www.odoo.com/documentation/18.0/developer/reference/external_api.html> | 2026-09-23 |
| 2 | `purchase.request` field names | `requested_by` (Many2one res.users, required), `assigned_to` (Many2one res.users), `picking_type_id` (Many2one stock.picking.type, required), `state` (draft/to_approve/approved/in_progress/done/rejected), `line_ids`. No department or analytic field natively | <https://github.com/OCA/purchase-workflow/blob/19.0/purchase_request/models/purchase_request.py> | 2026-09-23 |
| 3 | `purchase.request.line` field names | `product_id` (required), `product_qty`, `estimated_cost`, `date_required`, `supplier_id`, `purchase_lines` (M2M purchase.order.line -- how a line traces to the RFQs/POs generated from it via the module's "Create RFQ" wizard) | <https://github.com/OCA/purchase-workflow/blob/19.0/purchase_request/models/purchase_request_line.py> | 2026-09-23 |
| 4 | `hr.expense` field names | `employee_id` (required), `product_id` ("Category", domain `can_be_expensed=True`), `total_amount_currency` (Monetary, the field to set -- `price_unit`/`quantity` are computed from it), `currency_id` (required), `state` (draft/submitted/approved/posted/in_payment/paid/refused), `attachment_ids` (One2many ir.attachment, `res_model='hr.expense'`) | <https://github.com/odoo/odoo/blob/19.0/addons/hr_expense/models/hr_expense.py> | 2026-09-23 |
| 5 | No native vehicle field on `hr.expense` | Fleet app is not installed at the customer, so there is no per-vehicle analytic account to attach to (the usual Odoo mechanism for "cost against a vehicle"). Went with a plain `x_fms_vehicle_ref` char field on both `purchase.request` and `hr.expense` instead of building an analytic-account-per-vehicle scheme -- FMS remains the real source of truth for per-vehicle cost history (`expense_entries.vehicle_id`); this field is for GL/reference only | customer confirmed (no Fleet app) | 2026-09-23 |
| 6 | Odoo 19 `models.Constraint` | `_sql_constraints` no longer works in 19 -> `models.Constraint` class attributes everywhere, matching `shopify_bisync`'s and `x_ke_vat`'s scaffolds | <https://www.odoo.com/documentation/19.0/developer/reference/backend/orm/changelog.html> | 2026-09-23 |
| 7 | Odoo 19 `res.groups.privilege_id` | `category_id` on `res.groups` was replaced by `privilege_id` pointing at a `res.groups.privilege`; `security/fms_connector_security.xml` follows the same shape as `shopify_bisync/security/connector_security.xml` | <https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html> | 2026-09-23 |
| 8 | `web.base.url` is unsafe for outbound links | Confirmed prior finding (this shop's own `odoo-public-url-traps` note): `web.base.url` is rewritten on every admin login and is wrong for a link that goes out in an email. Added an explicit `fms_connector.public_base_url` override that both `_fms_record_url()` methods prefer | internal, re-confirmed against `ir.config_parameter` behavior in the 19.0 source | 2026-09-23 |
| 9 | Settings view: standalone app block | **Corrected 2026-09-23.** `base.res_config_settings_view_form` is a bare `<form>` with no `div.settings` wrapper anywhere in it or in how any other module extends it (verified against `purchase`'s own settings view, which targets the form element itself). The original `div[hasclass('settings')]` xpath matched nothing and would have failed module installation outright. Now targets the form element directly, same as `purchase`/`sale`/etc. | <https://github.com/odoo/odoo/blob/19.0/odoo/addons/base/views/res_config_settings_views.xml>, <https://github.com/odoo/odoo/blob/19.0/addons/purchase/views/res_config_settings_views.xml> | 2026-09-23 |
| 10 | Employee/requestor resolution | `requested_by` on `purchase.request` is `res.users` (matched by `login`/`email` ilike); `employee_id` on `hr.expense` is `hr.employee` (matched by `work_email`/`user_id.login` ilike). Both fall back to a configured default when no match, rather than failing the push outright | field types per items 2 and 4 above | 2026-09-23 |
| 11 | Callback delivery | Best-effort only in this first cut (`requests.post`, 10s timeout, logged failure, no retry). If FMS reachability proves flaky in practice, promote to a queued job (small log table + `ir.cron` sweep) rather than tightening the timeout | design choice, matches the scope agreed for this phase | 2026-09-23 |
| 12 | `hr.expense` needs an explicit submit step | **Bug found and fixed 2026-09-23.** A bare `create()` left every pushed expense stuck in `state == 'draft'` forever on both versions -- nobody was ever notified, and there was nothing for Rose to approve. Odoo 19 removed `hr.expense.sheet` entirely (`action_submit()` now lives directly on `hr.expense`, auto-approving when the employee has no expense manager); Odoo 18 computes `state` purely from a linked `hr.expense.sheet` and has no direct action on `hr.expense` at all. `_fms_submit_for_approval()` branches on `hasattr(self, 'action_submit')` to call the right one. Also confirmed `action_submit()`'s own permission check (`user.employee_id != expense.employee_id and not expense.can_approve`) does not block a `.sudo()` call: `can_approve`'s compute explicitly treats `self.env.su` as sufficient | <https://github.com/odoo/odoo/blob/19.0/addons/hr_expense/models/hr_expense.py>, <https://github.com/odoo/odoo/blob/18.0/addons/hr_expense/models/hr_expense_sheet.py> | 2026-09-23 |
| 13 | `fms.connector.branch.route.country` `required=True` contradicted its own help text | **Bug found and fixed 2026-09-23.** The field's help text documented "leave country blank to match any country for this branch," but `required=True` makes Odoo's ORM reject a blank value on save -- the wildcard-country fallback branch in `resolve_approver()` could never actually be reached. Removed `required=True` | internal contradiction, no external doc needed | 2026-09-23 |

## Open items for the customer's Odoo team to confirm before go-live

1. That `purchase_request` (OCA/purchase-workflow) is in fact what's
   installed, and that the field names in row 2/3 above match their actual
   installed revision.
2. A picking type and a placeholder product for the settings screen
   ("Purchase request picking type" / "Purchase request product") --
   `purchase.request.line` requires both even for a service-only line like a
   vehicle repair.
3. An expense category product for "Expense category" in settings
   (`can_be_expensed = True`).
4. Row 12: on Odoo 19, an expense auto-approves itself on submit when the
   resolved employee (the real match, or the configured fallback) has no
   `expense_manager_id`/department manager set -- confirm the fallback
   expense employee has a manager configured, or Rose's approval step will
   be skipped entirely rather than just automated.
