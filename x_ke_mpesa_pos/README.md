# M-Pesa at the Point of Sale: `x_ke_mpesa_pos`

An M-Pesa payment terminal for each till. The cashier sends the payment line
and chooses one of three ways to take the money. At closing, the session is
settled receipt by receipt against the till's statement lines. Built on
[`x_ke_mpesa`](../x_ke_mpesa/README.md): the receipts, journals and matching
are its own; this module adds the POS side only.

## Setting up an outlet

1. Set up the till in `x_ke_mpesa`: shortcode under its store, *Set up
   accounting*, *Register C2B URLs*.
2. On the shortcode's **Outlet** tab, list the **Points of sale** that may
   collect into this till (and the outlet staff and supervisor).
3. **Create POS payment method** (shortcode header) makes "M-Pesa <till>":
   terminal *M-Pesa*, the till's journal, and its outstanding-receipts
   account. Add it to the point of sale's payment methods. One method per
   till; a point of sale can have several (a counter with two tills).
4. Turn on **Cash Rounding** to 1.00 for *all* payment methods of that point
   of sale. M-PESA takes no cents, and the payment method shows a warning
   until rounding is set.
5. Per method, choose what the cashier may do: *Prompt the customer's phone*,
   *Pick a payment made to the till* (exact amount only, unless *Offer
   receipts of any amount*), *Type a receipt number*.

Odoo refuses a method whose journal is not its till's journal, or a point of
sale that is not on the till's list. The server checks each call against the
session too, so a cashier at Outlet X cannot collect into Outlet Y's till or
take its receipts.

**Choice Feeds** (HQ and 5 outlets, 6 Buy Goods tills under one store) has 6
points of sale, each with its own "M-Pesa <till>" method. The tills' journals
roll up to one company. Each outlet sees its own till; the accountant sees
all of them.

## At the till

Choose the M-Pesa method, check the amount, press **Send**. A dialog offers:

* **Prompt phone**: type the customer's number (taken from the customer, when
  there is one). An STK Push goes to the phone, and the line completes by
  itself when the customer enters their PIN; the server pushes the outcome to
  the POS, with a poll every 3 s in case that message is lost. A cancelled,
  failed or wrong-PIN prompt shows Safaricom's reason. If nothing is heard
  after two and a half minutes the line gives up: if the customer was charged
  after all, the payment shows under *Paid to till*.
* **Paid to till**: the customer paid the till from their phone. The list
  shows this till's unclaimed payments of the last 30 minutes (Settings ›
  *Claim window*), and payments Safaricom could only place on the store:
  time, first name, masked number and the receipt's last characters. Picking
  one claims it, and no other order can claim it too.
* **Type receipt**: for when Daraja or the network is down and the customer
  shows the SMS. The ticket prints the receipt with a `*`. It stays flagged
  until M-PESA confirms the same receipt and amount on this till. After 30
  minutes without that, the till's supervisor gets an activity and a
  notification. Offline, this is the only choice. The receipt is recorded when
  the order syncs.

The ticket prints `M-PESA <receipt>` under the payment line. **Force done** on
an M-Pesa line asks for a receipt number (the same as *Type receipt*).
**Cancel** on a waiting line releases what it held; a payment picked for an
order that is then abandoned stays held until the session closes. The terminal
rounds a line with cents down
to whole shillings and asks for the cents in cash. Refunds are not paid from
the terminal. Refund from the back office (B2C refunds come with
`x_ke_mpesa_b2c`).

## Closing the session

When the session closes, each M-Pesa payment in it is reconciled with its
receipt's statement line, through `account_reconcile_oca`, in the till's
journal. The session form then shows:

| Field | Meaning |
|---|---|
| M-Pesa expected | What the session's M-Pesa payments add up to |
| M-Pesa confirmed | What M-PESA has confirmed of it (receipts matched) |
| M-Pesa difference | Expected minus confirmed: typed receipts not confirmed yet, or a dispute |
| Unconfirmed M-Pesa receipts | How many receipts still wait for M-PESA |

A typed receipt that M-PESA confirms later (IPN, Pull API or statement file)
is reconciled against the closed session at once, and the difference
shrinks. Claims left by orders that were never paid are released at closing;
an hourly job releases any that remain.

## Reports

*Reporting › M-Pesa › POS Sessions*: expected, confirmed and difference per
session, point of sale and till. *STK performance* (from `x_ke_mpesa`) shows
prompts, successes, cancellations and time to pay per outlet and cashier.

## Access

Cashiers need *Point of Sale: User*. They do not need any accounting right or
M-Pesa group to take payments. Give them *M-Pesa: Outlet* only if they should
also see their till's receipts in the back office.

## Training and rehearsal without Safaricom

`tools/mpesa_fake_daraja.py` runs the fake Daraja of the tests as a server.
Every prompt is "paid" a few seconds after it is sent, and a customer paying
a till is one `curl` away. Point a test database's M-Pesa app at it (consumer
key `ck_test`, secret `cs_test`, store 174379 with the public sandbox passkey)
and cashiers can practise all three ways.

`tools/mpesa_pos_e2e.js` drives a real browser (Playwright) through the three
ways, against that fake, and checks the tickets:

```
python3 tools/mpesa_fake_daraja.py &
ODOO_URL=http://127.0.0.1:8069 POS_CONFIG_ID=1 METHOD="M-Pesa 600100" TILL=600100 \
PRODUCT="Layers mash 50kg" PRICE=2500 node tools/mpesa_pos_e2e.js
```

## Tests

```
odoo -d test -i x_ke_mpesa_pos --test-tags /x_ke_mpesa_pos --stop-after-init
```

The tests cover: wiring and the outlet restriction, rounding, an STK sale
settled at closing, a failed prompt, cents, another outlet's call refused,
paid-to-till claims (wrong amount, store receipts, release), typed receipts
(confirmed after closing, offline, already used, another till's), one receipt
for two orders, and an abandoned claim released at closing.

## Licence

LGPL-3.
