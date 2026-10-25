# M-Pesa (Daraja) for Accounting: `x_ke_mpesa`

Every M-Pesa till, store and paybill becomes a bank journal in Odoo 19
Community. Every confirmed receipt becomes **exactly one** statement line in
that journal (`unique_import_id = MPESA-<receipt>`), whichever channel saw it
first: the C2B confirmation ("IPN"), the STK Push callback, the Pull API or a
statement file. Lines are matched to invoices, portal payments and POS
sessions through `account_reconcile_oca`, and what is left waits in a queue.

Design, decisions and open questions: [`docs/mpesa/PLAN.md`](../docs/mpesa/PLAN.md).

| Module | What it adds |
|---|---|
| `x_ke_mpesa` | This module: apps, shortcodes, callbacks, statement lines, matching, invoice STK |
| `x_ke_mpesa_pos` | M-Pesa POS terminal: STK, "paid to till" picker, manual entry, session reconciliation |
| `x_ke_mpesa_statement` | Pull API (48 h backfill) and the M-PESA Org Portal statement import |
| `x_ke_mpesa_payment` | "Pay now" by STK on the portal and website |
| `x_ke_mpesa_etims`, `x_ke_mpesa_sms`, `x_ke_mpesa_mis` | Bridges, installed automatically |
| `x_ke_mpesa_b2c` | Refunds (B2C v3), supplier payments (B2B), reversals |

## Where things are

* **Invoicing › Configuration › M-Pesa**: Daraja Apps, Shortcodes, Callbacks.
* **Invoicing › Accounting › M-Pesa Receipts**: the queue (unallocated by
  default; filters for exceptions, parked, claimed).
* **Invoicing › Reporting › M-Pesa**: daily collections, till reconciliation
  (red / green), unallocated ageing, exceptions, callback health, last
  callback per till, customer payments.
* On a customer invoice: **Request M-Pesa payment** (amount editable, whole
  shillings).
* Settings › Invoicing › **M-Pesa**: suspense and rounding accounts, matching
  rules, fraud controls, retention, callback base URL and IP allow-list.

## Roles

| Group | Can |
|---|---|
| M-Pesa: Outlet | See the receipts of the tills they are listed on (Shortcode › Outlet › Outlet staff). No accounting. |
| M-Pesa: Officer | See all receipts of their companies, allocate, park, read reports. Every accountant is one. |
| M-Pesa: Manager | Configure apps and shortcodes; the only role that reads consumer secrets, passkeys and initiator credentials. Every Settings administrator is one. |

## Setting up

1. **Daraja app** (one per environment): name, environment, consumer key and
   secret, and the Safaricom certificate for that environment (PEM), which
   encrypts initiator passwords. *Test connection* fetches a token.
2. **Shortcodes**: a *store* (head office) with its passkey, its *tills*
   under it, or a *paybill* with its passkey. A till uses its store's
   passkey. Defaults for a till's STK Push: `BusinessShortCode` = store,
   `PartyB` = till, `CustomerBuyGoodsOnline`.
3. **Set up accounting** on each shortcode creates its journal
   ("M-Pesa Till 123456 - Outlet X") with its own bank account and
   outstanding-receipts account, and the shared *M-Pesa suspense* account as
   the journal's suspense account.
4. **Register C2B URLs** on each till or paybill (or on the store, if that is
   what Safaricom accepts: set *C2B registration shortcode*).
5. Background jobs run on the `root.mpesa` queue_job channel. Give it
   capacity on the server, e.g. `channels = root:4,root.mpesa:2`, and make
   sure the queue_job runner is enabled (`server_wide_modules =
   base,web,queue_job`).

### Callback URLs

`https://<host>/pay/ke/<token>/c2b/confirm` (and `/c2b/validate`, `/stk`,
`/status/result`, `/status/timeout`, `/pull`). The token is 32 random hex
characters per shortcode; *New callback token* replaces it (then register
again). Safaricom refuses URLs containing `mpesa`, `m-pesa`, `safaricom`,
`exe`, `exec`, `cmd`, `sql` or `query` in any case, anywhere, host name
included. The shortcode form shows a red box when the URL would be refused.

The werkzeug access log masks tokens (`/pay/ke/…cdef/c2b/confirm`).

**One database per host name.** Safaricom sends no cookie and no
`X-Odoo-Database` header, so Odoo must be able to pick the database from the
host name alone: a single database, or `dbfilter = ^%d$` / `^%h$` with a host
name per database. On a shared host name, have the reverse proxy set
`X-Odoo-Database: <db>` for `/pay/ke/`. The shortcode form warns when the
callback host does not lead to its database alone.

## How a receipt flows

1. Safaricom posts; the controller stores the raw body, the source address and
   a dedup key, queues a job and answers `{"ResultCode": 0, "ResultDesc":
   "Accepted"}`, in milliseconds.
2. The job upserts the receipt (one per company, under an advisory lock),
   creates the statement line in the till's daily statement, and runs the
   matching rules (each can be switched off):
   1. an STK receipt pays the document it was requested for;
   2. a POS "paid to till" claim settles at session close (`x_ke_mpesa_pos`);
   3. a paybill account number equal to an invoice number pays that invoice;
      equal to a customer's *M-Pesa account number* (or internal reference)
      it pays their oldest open invoices, the rest on account;
   4. anything else waits in the queue: **Allocate** (invoices, customer on
      account) or **Park** with a reason.
3. STK requests without a callback after 90 s are asked about with STK Query,
   whose answer is final. Above *Double-check STK above*, a callback is not
   believed until STK Query agrees (callbacks are not signed).

## Fraud controls

* A receipt number is used once per company; a receipt paid to another till
  than the one claimed is flagged *cross-till*.
* Manual receipts (Daraja down, the customer shows an SMS) stay flagged until
  M-PESA confirms the same receipt and amount on the same till. After 30
  minutes the till's supervisor gets an activity and a notification.
* Optional source-address allow-list (seeded with the community-documented
  196.201.212.0/24, 196.201.213.0/24, 196.201.214.0/24: confirm with
  Safaricom). Callbacks from elsewhere are kept but not processed.
* Optional Transaction Status check of manual receipts.
* Full phone numbers from STK Push are masked after the retention period
  (90 days by default), and raw callbacks redacted.

## Go-live checklist, per till

- [ ] Daraja go-live done for the store/paybill; production consumer key and
      secret on a **production** Daraja app; passkey received by email and
      entered on the store or paybill.
- [ ] Production certificate pasted on the production app.
- [ ] API initiator user created on the M-PESA Org Portal (roles:
      Transaction Status query ORG API, plus B2C / reversal roles if used);
      initiator name and password entered on the shortcode.
- [ ] *Set up accounting* done: journal, bank and outstanding accounts.
- [ ] Callback URL has no red box (HTTPS, port 443, no forbidden word,
      host name leads to this database alone).
- [ ] **C2B URLs registered.** Production registration is one-time: changing
      the URLs later means deleting them under Daraja › Self Services › URL
      Management (two Business Administrator/Manager approvals) or emailing
      apisupport@safaricom.co.ke.
- [ ] Pull API registered (`x_ke_mpesa_statement`; production only, one-time,
      with the nominated number).
- [ ] **KES 1 STK test** from a real phone lands in this till's journal. Error
      2028 / SFC_IC0003: swap the STK BusinessShortCode / PartyB.
- [ ] **KES 1 C2B test** (pay the till from a phone) lands in this till's
      journal, with the right till even if the registration is per store.
- [ ] POS payment method mapped to this till (`x_ke_mpesa_pos`).
- [ ] Outlet users listed under *Outlet staff*, supervisor set; outlet users
      hold *M-Pesa: Outlet* only.
- [ ] Shortcode state set to **Live**.

## Tests

`x_ke_mpesa/tests/fake_daraja.py` is a Daraja on 127.0.0.1: token (each new
token invalidating the last), STK Push and Query, C2B register and simulate,
Transaction Status and Pull, with callbacks that the test delivers, drops,
duplicates, delays or reorders.

```
odoo -d test -i x_ke_mpesa,l10n_ke --test-tags /x_ke_mpesa --stop-after-init
```

`tools/mpesa_sandbox.py` runs the same calls against Safaricom's real sandbox
(STK on 174379 with the public passkey, C2B simulate) from any machine that can
reach it. `tools/mpesa_fake_daraja.py` runs the fake as a server, for training
and rehearsals on a test database (see `x_ke_mpesa_pos/README.md`).

## Licence

LGPL-3. Depends on OCA `account_reconcile_oca` (AGPL-3), so an OPL-1 module
cannot depend on this one; bridges depend on both instead.
