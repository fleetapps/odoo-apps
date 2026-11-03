# Petty Cash

The petty cash book your team keeps in a spreadsheet, inside Odoo 19 Community,
with the same columns and a tab per month.

## What people do

* **Type a payment in the last row.** Date (type `3`, `3/11` or `3 Nov`), what it
  was for, paid to, phone, M-Pesa or cash, M-Pesa code, amount. Enter moves to the
  next cell; Enter on *Paid* saves and starts the next row. `N` jumps to a new row,
  `/` to search.
* **Paid to remembers people.** Typing a few letters offers payees paid before,
  with their phone and what they are usually paid for.
* **The category picks itself** from the matching rules in
  *Accounting > Configuration > Reconciliation Models* (the `Auto: ...` rules), in
  their order: the description first, then description + payee. It can be changed
  per row.
* **Charges fill in** for send-money payments (Safaricom tariff); pay bill and till
  charges come with the statement.
* **Each row says what it still needs**: amount, what for, category, product and
  quantity (stock bought with petty cash), receipt (cash payments). Ready rows are
  approved one by one from the side panel, or all at once.
* **Approving posts one journal entry per voucher** in the float's journal:
  expense (or stock), M-Pesa charge, float. Receipts are copied onto the entry.
* **Stock bought with petty cash** (rules listed on the float, e.g. Raw materials,
  Packaging) asks for the product and quantity and is received into the main
  warehouse at what was paid, so it counts in cost of goods.
* **Add float** records money sent to the float (posted at once for approvers).
* **Upload statement** (weekly): the personal M-PESA statement PDF (with its
  password) or the same table as CSV/XLSX. Typed payments are confirmed by code,
  or by amount within a day, and get the real charge and the statement balance;
  payments nobody typed come in as rows to describe. The book shows whether the
  float balance agrees with the statement.
* **Export** the month, or the whole year with a tab per month, to Excel.

## Set up

1. *Petty Cash > Floats*: journal (e.g. Petty Cash, PCSH), M-Pesa charges account,
   charges paid to (Safaricom), top-ups come from (e.g. Director's current
   account), rules that buy stock, approvers.
2. Give people *Petty Cash: Cashier* (type, upload) or *Approver* (approve, set up).
   Accounting administrators are approvers.

## Notes

* Vouchers are numbered per float and month: `PCV/2026/11/001`.
* An approved voucher is locked; *Reset to draft* cancels its entry (not allowed
  once it received stock - return the goods first).
* Each M-Pesa code can be in the book once.
