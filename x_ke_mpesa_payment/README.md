# M-Pesa online payments: `x_ke_mpesa_payment`

"Pay now" with M-Pesa on the customer portal and the website. The customer
types their number, Odoo sends an STK Push prompt to that phone, and Odoo's
payment status page waits for M-PESA's answer. Built on
[`x_ke_mpesa`](../x_ke_mpesa/README.md).

## Setting up

1. Set up the till or paybill in `x_ke_mpesa` (journal, C2B URLs, and the
   passkey of its store or paybill).
2. Invoicing › Configuration › Payment Providers › **M-Pesa**: choose the
   **M-Pesa till or paybill** on the Credentials tab, then set the state to
   *Test* or *Enabled*. The provider's payment journal becomes that
   shortcode's journal, its payments wait on the shortcode's outstanding
   receipts account, and Odoo's *M-Pesa* payment method (Kenya, shillings) is
   switched on.

Synad Properties would use its paybill (the invoice number is the account
reference). Choice Feeds would use the till that online orders pay into.

## How a payment goes

1. The customer picks M-Pesa, types their number and presses Pay. Odoo creates
   the payment transaction, and the form asks `/payment/mpesa/stk` to prompt
   the phone. That route only acts with a token Odoo gave the same form, so
   nobody can prompt phones for other people's payments. A double click
   prompts once.
2. The transaction is *pending* ("enter your M-PESA PIN"), and the status
   page polls it.
3. The STK callback arrives through the receipt log, like any other:
   * paid: the transaction is *done*, its provider reference is the receipt
     number, and Odoo creates and posts the payment;
   * cancelled on the phone (1032): *cancelled*; any other failure (wrong PIN,
     no funds, timeout): *error* with Safaricom's reason, and the customer can
     try again.
4. The receipt's statement line is reconciled with that payment on the
   outstanding receipts account, matched by receipt number. Until the payment
   exists, no other matching rule may take the receipt, or the invoice would be
   paid twice. The invoice ends up *paid*.

M-PESA takes whole shillings: an invoice of KES 1,000.40 is prompted for
1,000, the payment is for 1,000.40, and the 0.40 goes to the M-Pesa rounding
account when the receipt meets the payment. A receipt for a different amount
(the customer paid less) leaves the transaction in *error* and the receipt in
the M-Pesa Receipts queue, flagged *Amount differs*.

## Tests

```
odoo -d test -i x_ke_mpesa_payment --test-tags /x_ke_mpesa_payment --stop-after-init
```

Provider wiring, an invoice paid end to end (prompt, callback, payment, bank
reconciliation), cents, a cancelled prompt, a wrong PIN, a different amount,
a double click, Daraja refusing, a bad number, a processed payment not
prompted again, and the prompt route refusing a forged token (over HTTP).

## Licence

LGPL-3.
