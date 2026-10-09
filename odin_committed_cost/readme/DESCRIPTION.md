Budget against **committed** as well as actual.

A project reported on budget while half a million of confirmed purchase orders
sit unbilled is not on budget — it just has not been invoiced yet. Committed
cost is the leg that makes the answer true, and Odoo Community has no budgeting
at all to hang it from.

Confirming a purchase order records a commitment against each analytic account
on its lines. Billing moves that value from committed to actual. The same money
is never counted twice, and a budget shows all three figures side by side, by
workstream.

The budget model here is deliberately small and purpose-built. The OCA answer,
mis_builder_budget, is AGPL, and depending on it would stop an OPL-1 app being
able to depend on this one.
