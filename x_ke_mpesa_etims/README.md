# M-Pesa and eTIMS: `x_ke_mpesa_etims`

Installed by itself when [`x_ke_mpesa_pos`](../x_ke_mpesa_pos/README.md) and
`x_ke_etims_pos` are both installed.

eTIMS guesses each POS payment method's KRA payment type from its name, and
takes any method with a payment terminal for a card (05). An M-Pesa terminal
is mobile money (06) whatever the method is called, so a till's method named
"Westlands counter" is reported correctly. A type set by hand on the method
still wins.

Invoices need nothing from here: eTIMS fixes an invoice's payment type when
the invoice is posted (credit for an invoice, cash for a sales receipt),
before any M-Pesa payment of it.
