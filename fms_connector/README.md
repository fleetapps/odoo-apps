# Fleet FMS Connector

A narrow bridge between the Fleet FMS app and Odoo. It does **not** move
RFQs, vendor quotations, bid analysis or PO/expense approval into FMS --
those stay exactly where they are today, in Odoo. It only carries two
things across the boundary:

1. **FMS -> Odoo**: a driver's purchase request becomes a `purchase.request`
   (see the `purchase_request` OCA module) with enough reference fields
   (driver, vehicle, branch, country) to trace it, an employee resolved by
   email, and an approver assigned by branch/country routing.
2. **Odoo -> FMS**: when the resulting purchase order is confirmed, FMS is
   told the winning vendor and approved amount, so it can let the
   admin/driver proceed with the vehicle.
3. **FMS -> Odoo**: once the vehicle is serviced and the vendor invoice is
   uploaded to FMS, a draft `hr.expense` is created in Odoo (one line, no
   labor-hour/rate breakdown) with the invoice attached, for approval inside
   Odoo exactly like any other expense.

FMS sends its own approval-request emails (with a link straight into the
Odoo record). This module never sends email on its own -- it only returns
the record's URL so FMS can build that link.

## Dependencies

- `purchase_request` (OCA/purchase-workflow) -- must already be installed;
  this module extends it rather than shipping its own purchase-request flow.
- `hr_expense`, `mail` (core Odoo).
- Python `requests` on the server.

## Setup (Settings > Fleet FMS Connector)

1. **Generate an inbound API key** and paste it into FMS's own Odoo
   integration settings (`organization_integrations`, `integration_type =
   'odoo'`) -- FMS sends it as `X-FMS-Api-Key` on every call in.
2. **FMS base URL + outbound API key** -- used when this module calls back
   into FMS after a PO confirms. The outbound key is the FMS organization's
   own `api_key` (the same one its other inbound integrations validate
   against).
3. **Public URL** -- set this explicitly. Do not leave it to `web.base.url`;
   that System Parameter gets rewritten to whatever host an admin last
   logged in from, which is exactly wrong for a link that goes out in an
   email.
4. **Fallbacks**: a requestor, an approver, and an expense employee to use
   when an FMS email doesn't match any Odoo user/employee.
5. **Purchase request picking type + product**: `purchase.request.line`
   requires both even for a service-only line (a vehicle repair isn't an
   inventory movement) -- point these at whichever operation type / generic
   service product your team uses for non-stock requests.
6. **Expense category product**: any product with `can_be_expensed = True`,
   e.g. "Vehicle Maintenance & Repair".
7. **Branch Routing** (Fleet FMS Connector menu): map country/branch ->
   approver for requests that need someone other than the instance-wide
   default.

## API

Both endpoints are upserts keyed on an FMS-supplied `fms_ref`, so a retried
call after a network failure never creates a duplicate.

`POST /fms_connector/api/v1/purchase_requests`
```json
{
  "fms_ref": "<uuid>",
  "requested_by_email": "driver@example.com",
  "requested_by_name": "Jane Driver",
  "country": "Kenya",
  "branch": "Nairobi Program Office",
  "vehicle_ref": "KDA 123B",
  "description": "Brake pads replacement + service",
  "estimated_cost": 450.00,
  "currency": "KES",
  "date_required": "2026-10-01"
}
```
-> `{"id": 42, "name": "PR00042", "state": "to_approve", "url": "..."}`

`POST /fms_connector/api/v1/expenses`
```json
{
  "fms_ref": "<uuid>",
  "purchase_request_fms_ref": "<uuid, optional>",
  "employee_email": "admin@example.com",
  "employee_name": "Jane Admin",
  "vehicle_ref": "KDA 123B",
  "vendor_name": "Acme Motors",
  "description": "Brake pad replacement — Job Card #JC-0091",
  "amount": 430.00,
  "currency": "KES",
  "expense_date": "2026-10-05",
  "attachment_filename": "invoice.pdf",
  "attachment_base64": "..."
}
```
-> `{"id": 88, "state": "draft", "url": "..."}`

Both require header `X-FMS-Api-Key: <inbound key>`.

## Outbound callback (Odoo -> FMS)

On PO confirmation, `POST {fms_base_url}/odoo-po-status-callback` with
header `x-api-key: <outbound key>`:
```json
{
  "fms_ref": "<uuid>",
  "odoo_purchase_request_id": 42,
  "state": "approved",
  "po_number": "P00123",
  "vendor_name": "Acme Motors",
  "approved_amount": 430.00,
  "currency": "KES"
}
```

See `docs/DOCS_REGISTER.md` for every platform assumption this was built
against and the open items to confirm before go-live.
