# M-Pesa suite for Odoo 19 Community: plan

Status: the build was asked for in full, so this plan is the design the code
follows rather than a proposal waiting for sign-off. Every milestone is its own
run of commits on `claude/ke-mpesa-suite`, so each can be reviewed (and
reverted) on its own. Questions only Safaricom can answer are listed in §9 and
are *designed around*, not guessed: wherever the answer changes behaviour there
is a setting, and the default is the one Safaricom's own documentation points
to.

First client: Choice Feeds (HQ + 5 outlets, 6 Buy Goods tills, STK Push and C2B
confirmation on every till). Second: Synad Properties (hotel, restaurant,
rentals: deposits and rent, mostly paybill). Built once, generic, LGPL-3, our
own code.

---

## 1. What was read, and what it changes

### 1.1 Fleet's M-Pesa code (`Fleet-ke/business-web`, Django)

`organization/mpesa.py`, `organization/views.py` (callbacks),
`api/driver/order/views.py` (call sites), `order/models.py` (`STKPushRequest`,
`MpesaPayments`), `organization/tests/test_mpesa_stability.py`, and commit
`f200f17` "make STK push resilient and payment-safe".

**Reused (ported, re-typed, not copied verbatim):**

| Fleet | Becomes | Why it survives |
|---|---|---|
| `requests.Session` + urllib3 `Retry`, GET retried, POST never retried on read/status errors | `x_ke_mpesa/lib/daraja.py` session | The production lesson: a read error on the STK POST happens *after* Safaricom got it. Retrying double-prompts the customer. Connection errors (request provably never sent) are still retried. |
| `MpesaRequestError` for transport failures | `DarajaTransportError` (outcome unknown) vs `DarajaError` (Safaricom said no) | Callers must tell "it failed" from "we don't know". |
| `getPassword` = base64(shortcode + passkey + timestamp) | `stk_password()` | Same formula, now with the timestamp in Africa/Nairobi time rather than server time. |
| `normalize_msisdn` (07…/7…/254…) | `normalize_msisdn()` | Extended to 01…/1…/+254 and to reject non-Safaricom lengths. |
| `short_account_ref` (alphanumeric, 12) and `TransactionDesc[:13]` | same limits | Daraja limits unchanged. |
| BuyGoods vs PayBill: `CustomerBuyGoodsOnline`, PartyB = till | STK settings on the shortcode | Confirmed by Daraja's own 2028 error text (§9.1). |
| STK Query by `CheckoutRequestID`, *not* Transaction Status, for STK | `stk_query()` | Fleet's comment is right and stays. |
| Aware datetimes from `TransactionDate` | `parse_daraja_time()` → UTC | Daraja times are EAT, naive. |

**Lessons that change the design (Fleet bugs we do not port):**

1. *A new token per call.* `getToken()` ran before every request. Daraja's
   FAQ says each token request invalidates the previous token, so two workers
   kept invalidating each other (→ `404.001.03 Invalid Access Token`). Tokens
   are now cached per app in the database, refreshed under a row lock, and a
   `404.001.03` triggers one refresh-and-retry, never a loop.
2. *Callback looked up by both ids and 404'd if absent.* A callback that beat our
   own save was lost. Callbacks now upsert on `CheckoutRequestID`.
3. *Failure callbacks answered `417`.* Safaricom treats non-2xx as delivery
   failure. Every callback is now acknowledged `ResultCode 0` once stored.
4. *Heavy work inside the callback request* (wallet maths, third-party HTTP).
   Callbacks now store the raw body and return; processing is a queue job.
5. *`eval()` on `AccountReference`.* Gone; nothing from a payload is evaluated.
6. *`print()` of whole payloads and responses.* Replaced by a logger that
   redacts `Password`, `SecurityCredential`, `Authorization`, passkeys and the
   callback token.
7. *Duplicate receipts only checked in one path.* The receipt number is now a
   unique key per company on every path (STK, C2B, Pull, statement, manual).
8. *`CheckMpesaPrePaidDetails` called `check_transaction_status` with the
   wrong signature* (manual verification never worked). Manual receipts are
   now verified by the IPN, with Transaction Status as an optional second check.
9. *Server-local timestamps.* `datetime.now()` on a UTC server produced a UTC
   STK timestamp. All Daraja timestamps are now explicitly Africa/Nairobi.

### 1.2 Our base and the installed stack

Read from this repository and confirmed on the connected Odin demo instance
(`ir.model.modules`):

* `x_ke_base` and `x_ke_vat` are **OPL-1** and keep AGPL out of their
  `depends`. The M-Pesa suite never depends on them, and they never depend on
  it (see §8).
* Installed: `account`, `l10n_ke`, Odoo Mates (`om_*`), OCA
  `account_statement_base`, `account_statement_import_base`,
  `account_statement_import_online`, `account_reconcile_oca`, `queue_job`,
  `partner_statement`, `mis_builder`, `account_payment`, `point_of_sale`,
  `pos_online_payment`, `access_manager_pro`.
* **Not installed / not found:** `account_statement_import_sheet_file` (pulled
  in by `x_ke_mpesa_statement`), any eTIMS module, `ke_sms_gateway`, and
  `docs/ke_etims/SPEC.md` (not on any branch of this repository). See §9.2.
* In 19.0, `unique_import_id` lives in OCA `account_statement_import_base`
  (LGPL-3), not in core, with a global unique constraint. So `x_ke_mpesa` also
  depends on `account_statement_import_base` (LGPL-3, only needs
  `account_statement_base`).
* That module rewrites `unique_import_id` to `<account>-<journal id>-<id>` on
  file and online imports. For journals linked to an M-Pesa shortcode an
  override keeps `MPESA-<receipt>` as is, so C2B, STK, Pull and file import
  all collide on the same key and the receipt exists once.

### 1.3 Daraja

`developer.safaricom.co.ke` and `sandbox.safaricom.co.ke` are not reachable
from this build environment. The Daraja 3.0 pages were read from a public
markdown mirror of the portal (scraped December 2025), and cross-checked
against Fleet's working calls. The facts the code relies on:

* OAuth: `GET /oauth/v1/generate?grant_type=client_credentials`, Basic auth,
  `expires_in` 3599. "Each request invalidates the previous token."
* STK Push `POST /mpesa/stkpush/v1/processrequest`; STK Query
  `POST /mpesa/stkpushquery/v1/query` (returns `ResultCode`, **no receipt
  number**). While pending, STK Query answers `errorCode 500.001.1001`
  "The transaction is being processed": that is "ask again", not a failure.
* STK callback: `Body.stkCallback`, `CallbackMetadata.Item[]` only on
  success (`Amount`, `MpesaReceiptNumber`, `TransactionDate`, `PhoneNumber`,
  sometimes `Balance`). Result codes 0, 1, 2, 3, 4, 8, 17, 1001, 1019, 1025,
  1032, 1037, 2001, 2028, 8006, SFC_IC0003.
* C2B v2: `POST /mpesa/c2b/v2/registerurl` (`ShortCode`, `ResponseType`
  Completed/Cancelled, `ConfirmationURL`, `ValidationURL`),
  `/mpesa/c2b/v2/simulate` (sandbox only). v2 posts a **masked** MSISDN
  (`2547 ***** 126`; v1 posted a SHA-256 hash). Production registration is
  one-time; "URLs are already registered" is `500.003.1001`.
* URL rules: HTTPS in production, public host, none of `M-PESA`, `mpesa`,
  `Safaricom`, `exe`, `exec`, `cmd`, `sql`, `query` in any case.
* Validation answer within ~8 s: `{"ResultCode": "0", "ResultDesc":
  "Accepted"}` or `C2B00011`…`C2B00016` to reject.
* Transaction Status `POST /mpesa/transactionstatus/v1/query`
  (`CommandID TransactionStatusQuery`, `IdentifierType 4`), async result on
  `ResultURL` with `ResultParameters` (`ReceiptNo`, `Amount`,
  `TransactionStatus`, `DebitPartyName`…).
* Pull: `POST /pulltransactions/v1/register` (one-time, production only,
  `NominatedNumber`), `POST /pulltransactions/v1/query` (`StartDate`,
  `EndDate`, `OffSetValue`), 48-hour window, rows `transactionId`, `trxDate`,
  `msisdn`, `transactiontype`, `billreference`, `amount`.
* Reversal `POST /mpesa/reversal/v1/request`, B2C **v3**
  `POST /mpesa/b2c/v3/paymentrequest` with a merchant-generated
  `OriginatorConversationID` (our idempotency key), B2B
  `POST /mpesa/b2b/v1/paymentrequest` (`BusinessPayBill`/`BusinessBuyGoods`).
* `SecurityCredential` = RSA (PKCS#1 v1.5) of the initiator password with the
  Safaricom certificate for the environment; sandbox and production
  certificates differ.

### 1.4 Patterns (LGPL Community, used as patterns only)

* `pos_razorpay`, `pos_adyen`: `use_payment_terminal` selection,
  `PaymentInterface`, `register_payment_method`, server push through
  `pos.config._notify()` + `this.data.connectWebSocket()` with polling as the
  fallback, `payment_status` states (`waitingCard`, `done`, `retry`…).
* `payment_razorpay` (+ `payment_demo`): provider `code`, direct flow,
  `_get_specific_processing_values`, `_search_by_reference`,
  `_extract_amount_data`, `_apply_updates`.
* OCA `account_statement_import_online` (provider `_obtain_statement_data`)
  and `account_statement_import_sheet_file` (mapping records) at 19.0.
* OCA `account_reconcile_oca`: programmatic reconciliation through
  `reconcile_data_info`, `_add_account_move_line()` and
  `reconcile_bank_line()`, so what our engine does is exactly what an
  accountant would do in the widget, and can be undone there.

---

## 2. Where it sits

M-Pesa is a bank-like account per till: it lives in Accounting.

* One shortcode (till / store / paybill) = one bank-type journal. Every
  confirmed receipt becomes **exactly one** bank statement line in that
  journal, `unique_import_id = "MPESA-<receipt>"`, whichever channel saw it
  first: C2B confirmation, STK callback, Pull API or statement file. That line
  is the single source of truth for money in.
* POS orders, invoices and portal payments only create Odoo's standard
  outstanding-receipt entries. Statement lines are reconciled against them
  through `account_reconcile_oca`, automatically only where a match is
  certain.
* Menus (Odoo 19 Community's root is "Invoicing"; Odoo Mates renames nothing
  we hang off):
  * Invoicing › Configuration › **M-Pesa**: Daraja Apps, Shortcodes, Settings.
  * Invoicing › Accounting › **M-Pesa Receipts**: queue (unallocated,
    exceptions, parked, all).
  * Invoicing › Reporting › **M-Pesa**: the reports in §6.
  * POS: Point of Sale › Configuration › Payment Methods (M-Pesa terminal).
* Outlet staff (group *M-Pesa: Outlet*) see only their own till's receipts,
  through a record rule on the shortcodes assigned to the user, and get no
  accounting menu. `access_manager_pro` can narrow further; nothing relies on
  it.

---

## 3. Modules

| Module | Depends | Licence | What |
|---|---|---|---|
| `x_ke_mpesa` | `account`, `account_statement_base`, `account_statement_import_base`, `account_reconcile_oca`, `queue_job`, `mail` | LGPL-3 | Apps, shortcodes, token cache, Daraja client, transaction log, public callbacks, statement lines, matching engine, C2B registration, STK Query, Transaction Status, invoice "Request M-Pesa payment", crons, reports 1–4, 6, 9, security |
| `x_ke_mpesa_pos` | `x_ke_mpesa`, `point_of_sale` | LGPL-3 | M-Pesa POS terminal: STK from the payment screen, "Paid to till" picker, manual entry, session reconciliation, receipt no. on the ticket, reports 5, 7 |
| `x_ke_mpesa_statement` | `x_ke_mpesa`, `account_statement_import_online`, `account_statement_import_sheet_file` | LGPL-3 | Pull API as an online statement provider (48 h backfill, hourly 2 h pull), Org Portal statement mapping, charges and sweeps, report 8 |
| `x_ke_mpesa_payment` | `x_ke_mpesa`, `payment`, `account_payment` | LGPL-3 | `payment.provider` "M-Pesa" (STK) for portal / website "Pay now" |
| `x_ke_mpesa_etims` | `x_ke_mpesa`, `x_ke_base` (auto-install) | LGPL-3 | M-Pesa journals map to eTIMS payment type 06 "Mobile money" (contract in §9.2) |
| `x_ke_mpesa_sms` | `x_ke_mpesa`, `sms` (auto-install) | LGPL-3 | Payment-received SMS through Odoo's SMS framework (where `ke_sms_gateway` plugs in, §9.2) |
| `x_ke_mpesa_mis` | `x_ke_mpesa`, `mis_builder` (auto-install) | LGPL-3 | MIS Builder templates for the collection reports (kept out of the core so the core does not need `date_range`/`report_xlsx`) |
| `x_ke_mpesa_b2c` (phase 2) | `x_ke_mpesa` | LGPL-3 | B2C v3 customer refunds, B2B supplier payments, reversals |

---

## 4. Models and fields (`x_ke_mpesa`)

### 4.1 `mpesa.app`: one Daraja app per environment

`name`, `company_id`, `environment` (sandbox / production), `consumer_key`,
`consumer_secret` (both *M-Pesa Manager* only), `api_url` (computed default
per environment, overridable: proxies and the test server), `certificate`
(PEM of the Safaricom certificate for this environment, used to encrypt
initiator passwords), `access_token` / `token_expires_at` (system-only; the
shared cache), `token_refreshed_at`, `shortcode_ids`, `active`.

Token rule: use the cached token until 60 s before expiry; refresh under
`SELECT … FOR UPDATE` so one worker refreshes and the rest wait and reuse; a
`404.001.03` / `401.001` / `400.003.01` invalidates and refreshes **once**;
refreshes within 10 s of the last one reuse it instead of invalidating it.

### 4.2 `mpesa.shortcode`: tills, stores, paybills

| Field | Notes |
|---|---|
| `kind` | `till` / `store` (head office) / `paybill` |
| `number` | the shortcode |
| `parent_id` | a till's store; `child_ids` |
| `company_id` | the outlet's company (each Choice Feeds outlet is an Odoo 19 *branch*, i.e. a child company) |
| `app_id` | Daraja app |
| `passkey` | held on the store or paybill; a till uses its parent's (`effective passkey`) |
| `stk_business_shortcode`, `stk_party_b`, `stk_transaction_type` | separate on purpose (§9.1). Till defaults: store number / till number / `CustomerBuyGoodsOnline`. Paybill: own number in both / `CustomerPayBillOnline` |
| `stk_query_delay` | 90 s: no callback by then → STK Query, whose answer wins |
| `stk_confirm_threshold` | STK above this amount is confirmed by STK Query before the order completes |
| `c2b_shortcode` | the number registered for C2B (till, store or paybill; default: own number) |
| `callback_token` | 32 hex characters, per shortcode, regenerable; hex cannot spell any forbidden word |
| `c2b_validation`, `c2b_response_type` | validation off by default; `Completed` default |
| `c2b_registered_at`, `c2b_confirmation_url`, `c2b_validation_url` | URLs computed from `web.base.url` + token, checked against Safaricom's rules before registering |
| `initiator_name`, `security_credential` | the credential is stored encrypted (it *is* the RSA ciphertext); the initiator password is a write-only field, encrypted on save with the app's certificate and never stored |
| `journal_id` | the till's bank journal |
| `analytic_account_id` | the outlet |
| `state` | sandbox / live |
| `last_callback_at`, `last_org_balance`, `last_org_balance_at` | from C2B confirmations |
| `trading_hours_from/to`, `supervisor_id`, `user_ids` | callback-gap report, manual-receipt alerts, outlet staff access |

A "Set up accounting" button creates the journal "M-Pesa Till 123456 -
Outlet X" with its own bank account, its own outstanding-receipts account, and
the company's shared **M-Pesa suspense** account as the journal suspense
account.

### 4.3 `mpesa.transaction`: the transaction log

One record per STK request and per receipt; a receipt is unique per company.

* Identity: `name`, `company_id`, `shortcode_id`, `receipt` (unique per
  company), `checkout_request_id` / `merchant_request_id` (unique where set),
  `idempotency_key` (unique where set).
* `source`: `stk` / `c2b` / `pull` / `statement` / `manual` / `status`
  (how we learned of it). `channel` for reports: `stk` / `till` (customer paid
  the till) / `paybill` / `manual`.
* `state`: `draft` → `requested` → `confirmed` | `failed`; `unknown` (STK
  sent, outcome not known); `manual` (cashier-entered, waiting for the IPN).
* Money: `amount` (whole shillings), `currency_id`, `trans_time` (UTC),
  `trans_date`, `org_balance`.
* Customer: `phone` (full, STK only, cleared after the retention period),
  `msisdn_masked`, `customer_name`, `partner_id`, `bill_ref`,
  `invoice_number`, `third_party_trans_id`, `transaction_type`.
* STK: `account_reference`, `description`, `result_code`, `result_desc`,
  `cashier_message`, `requested_at`, `confirmed_at`, `query_count`,
  `last_query_at`.
* Origin: `res_model` / `res_id` (generic), `move_id` (invoice), `user_id`.
* Accounting: `statement_line_id`, `allocation_state` (`unallocated`,
  `allocated`, `parked`), `park_reason`.
* Controls: `exception` (`none`, `cross_till`, `amount_mismatch`,
  `reused`, `unconfirmed_manual`, `double_claim`), `exception_note`,
  `manual_user_id`, `manual_entered_at`, `alerted_at`.

### 4.4 `mpesa.callback`: raw inbox

`kind` (`stk`, `c2b_confirm`, `c2b_validate`, `status_result`,
`status_timeout`, `pull`, later `b2c_*`, `reversal_*`), `shortcode_id`,
`payload` (raw body, redacted after the retention period), `remote_ip`,
`ip_trusted`, `received_at`, `state` (`new`, `done`, `error`, `ignored`),
`error`, `transaction_id`, `dedup_key`, `company_id`.

### 4.5 Extensions

* `account.journal`: `mpesa_shortcode_ids`, `is_mpesa`.
* `account.bank.statement.line`: `mpesa_transaction_id`.
* `res.company` / settings: `mpesa_suspense_account_id`,
  `mpesa_rounding_account_id`, the four matching-rule switches,
  `mpesa_claim_window` (minutes), `mpesa_manual_alert_minutes` (30),
  `mpesa_phone_retention_days`, `mpesa_ip_allowlist_enabled` +
  `mpesa_ip_allowlist`, `mpesa_transaction_status_check`.
* `res.partner`: `mpesa_account_ref` (the customer or unit reference paybill
  customers type as their account number, e.g. Synad's unit code).
* `account.move`: "Request M-Pesa payment" button and wizard.

---

## 5. Flows

### 5.1 STK Push (invoice, POS, portal)

1. Create `mpesa.transaction` (`draft`) with the amount, the phone normalised
   07…/01…/+254… → 2547…/2541…, the origin document and an idempotency key.
   An open request with the same key (double click, POS retry) is returned
   instead of prompting again.
2. Amounts are whole shillings. Invoices: the amount is rounded and, when the
   receipt leaves less than one shilling open, the difference is written off to
   the rounding account. POS: the terminal refuses decimals and the setup
   warns unless the POS rounds to 1.00 (§7.1). Decimals are never sent.
3. Call STK; store `MerchantRequestID` and `CheckoutRequestID` under an
   advisory lock on the CheckoutRequestID; state `requested`.
4. The callback (stored, acknowledged, then processed by a queue job) upserts
   on `CheckoutRequestID`: if our own save has not landed yet it creates a
   placeholder that our save merges into.
   * `ResultCode 0` → `confirmed` with receipt, amount, phone, time.
   * 1032 cancelled, 1037 phone unreachable, 1 insufficient funds, 2001 wrong
     PIN, 1019 expired, 1001 subscriber busy, 17 repeat within 2 minutes, … →
     `failed`, each with a plain cashier message. Failure callbacks carry no
     `CallbackMetadata`.
5. No callback after `stk_query_delay` → STK Query (cron every minute, a job
   per transaction); its answer is authoritative. STK Query has no receipt
   number: a "paid" answer leaves the transaction `confirmed` *awaiting
   receipt*, and the receipt is attached when the C2B confirmation or Pull row
   for the same till, amount and minute arrives.
6. Transport error after sending → `unknown`; never retried. If the customer
   pays anyway, the STK callback creates a placeholder, linked back by full
   phone + amount + 5 minutes.
7. Above `stk_confirm_threshold`, a callback is double-checked with STK
   Query before the transaction is reported paid.

### 5.2 C2B confirmation (the IPN), on every till and paybill

* Routes `/pay/ke/<token>/c2b/confirm` and `/c2b/validate`,
  `/pay/ke/<token>/stk`, `/pay/ke/<token>/status/result` and `/timeout`,
  `/pay/ke/<token>/pull`: `type="http"`, `auth="none"`, `csrf=False`, raw
  JSON body. The database is the request's (dbfilter / single database) or
  the `db` segment of the alternative `/pay/ke/db/<db>/<token>/…` route,
  for multi-database servers.
* Store the raw body, the source IP and a dedup key, enqueue, commit, answer
  `{"ResultCode": 0, "ResultDesc": "Accepted"}`; in well under 2 s.
* Validation (off unless Safaricom enabled it): answered in the request,
  accept by default.
* Fields kept: `TransactionType`, `TransID`, `TransTime`, `TransAmount`,
  `BusinessShortCode`, `BillRefNumber`, `InvoiceNumber`,
  `OrgAccountBalance`, `ThirdPartyTransID`, `MSISDN` (masked: never used to
  match), `FirstName` (+ middle/last when present).
* Routing: the token identifies the registration; if the body's
  `BusinessShortCode` is one of that registration's tills, the receipt goes to
  that till. If the confirmation cannot identify the till (store-level
  registration, §9.1), it lands on the registered shortcode and the cashier's
  pick decides which order and till it belongs to.

### 5.3 Matching (one engine; rules in order, each switchable)

1. **STK** receipts are already linked to their document.
2. **POS "Paid to till"**: the cashier picks from that till's unclaimed
   receipts of the last N minutes (amount, time, first name; the phone stays
   masked). Exact amount by default.
3. **Paybill** `BillRefNumber` = invoice number → that invoice;
   = customer or unit reference → that partner's oldest open invoices (FIFO,
   remainder on account); otherwise unallocated.
4. Everything else stays unallocated and shows in the queue: allocate wizard
   (invoice, customer on account, POS order) or park with a reason.

### 5.4 Accounting

* One statement line per receipt: date = TransTime (Nairobi date), partner if
  known, ref = receipt, narration = channel + till. Lines are grouped into one
  daily statement per till.
* Invoices: reconcile the line with the invoice receivable. Portal payments:
  with the outstanding line of the `account.payment` the payment module
  created, matched by receipt number.
* POS: at closing Odoo makes one combined payment per method per session
  (unless *Identify Customer* is on). The session's M-Pesa lines are
  reconciled as a group against that payment's outstanding line; any
  difference is reported on the session and in report 7.
* Charges and sweeps come from the Org Portal statement (M3): charges to an
  expense account, sweeps as internal transfers to the bank journal.
* Backfill (M3): hourly Pull for the last 2 hours per live shortcode; only
  receipts we do not have become lines. Daily: each till's Odoo balance vs the
  latest `OrgAccountBalance`.

---

## 6. Reports

Pivot / graph / list views over stored fields (so `ai_dashboards` reads them
through `_read_group`), plus MIS Builder templates in `x_ke_mpesa_mis`.

1. Daily collections by till, outlet and channel: `mpesa.transaction`.
2. Till reconciliation per day: confirmed vs allocated vs unallocated vs
   manual-unconfirmed, red / green: SQL view `mpesa.report.till.day`.
3. Unallocated receipts with ageing buckets: SQL view.
4. Exceptions: manual never confirmed, reused / cross-till, amount mismatch.
5. STK performance by outlet and cashier: requests, success, cancelled,
   timeouts, insufficient funds, wrong PIN, average seconds to confirm (M2).
6. Callback health: last callback per till, gaps during trading hours, failed
   callbacks and jobs: SQL view.
7. POS session close: expected M-Pesa vs confirmed per session (M2).
8. Settlements and charges per till per month (M3).
9. Customer payments by partner (paybill customers, tenants), opened from
   the partner next to the OCA statements.

---

## 7. Security and fraud controls

* Callback URLs: HTTPS, port 443, random hex token per shortcode, no forbidden
  word anywhere in the URL (checked, including the host). The werkzeug access
  log masks the token.
* Callbacks are not signed: the source IP is logged; an optional allow-list
  (off by default; seeded with the community-documented 196.201.212.0/24,
  196.201.213.0/24, 196.201.214.0/24, to be confirmed with Safaricom) marks or
  rejects others. STK above a threshold is confirmed by STK Query; C2B receipts
  can be confirmed by Transaction Status.
* A receipt number is used once per company. A receipt confirmed on a
  different till than the one claimed is flagged `cross_till`.
* Manual receipt entry (Daraja down) is allowed, flagged until an IPN with the
  same receipt and amount arrives; still unconfirmed after 30 minutes → the
  outlet supervisor gets an activity and a bus notification (fake M-Pesa SMS
  fraud).
* Consumer secret, passkey, security credential: *M-Pesa Manager* only, never
  logged, redacted from request logs. The initiator password is never stored.
* Tokens cached per app (§4.1).
* Multi-company record rules on every model. Full STK phone numbers are kept
  only as long as `mpesa_phone_retention_days` allows (Data Protection Act),
  then masked; raw payloads are redacted on the same schedule.

Groups: *M-Pesa: Outlet* (own tills' receipts, POS use), *M-Pesa: Officer*
(all receipts of allowed companies, allocate, park), *M-Pesa: Manager*
(configuration and secrets).

### 7.1 POS rounding

Odoo 19 POS rounds per config (`cash_rounding`, `only_round_cash_method`).
M-Pesa needs whole shillings, so the M-Pesa payment line refuses decimals and
offers the whole-shilling amount; the setup check warns while the POS does not
round to 1.00 for all methods.

---

## 8. Licensing

* Every module here is LGPL-3 and ours. Nothing is copied from OPL apps.
  Community LGPL modules were read as patterns only.
* `x_ke_mpesa` depends on `account_reconcile_oca` (AGPL-3), as briefed. That
  is fine for an LGPL module, but it means an **OPL-1 app may not depend on
  `x_ke_mpesa`**: the house rule in `x_ke_base`/`odin_letter_of_credit`.
  That is why the eTIMS link is a bridge: neither side depends on the other.
* `queue_job`, `account_statement_base`, `account_statement_import_base`
  are LGPL-3; `account_statement_import_online`,
  `account_statement_import_sheet_file`, `mis_builder` are AGPL-3.

---

## 9. Open questions

### 9.1 Only Safaricom can confirm (designed for both, not guessed)

1. **Store numbers:** do Choice Feeds' 6 tills sit under one store number or
   six? Each till holds its own parent; passkey and STK `BusinessShortCode`
   follow the parent.
2. **Which shortcode a till payment's C2B confirmation carries** (till or
   store), and whether C2B can be registered per till. `c2b_shortcode` is
   configurable; routing uses the per-registration token first and the body's
   `BusinessShortCode` second (§5.2).
3. **STK on tills:** BusinessShortCode = store, PartyB = till is the default
   (Daraja's 2028 text). A KES 1 live test per till decides.
4. **Does an STK payment also fire the C2B confirmation?** Either way the
   receipt exists once.
5. **Pull API:** is `trxDate` UTC (it ends in `Z`) or EAT? Page size and
   `OffSetValue`? Which number is the `NominatedNumber`? Register per till or
   per store? `trxDate` handling is a setting (default: as given).
6. **Callback source IP ranges.**
7. **Org Portal statement export columns**: the mapping follows the standard
   export (Receipt No., Completion Time, Details, Transaction Status, Paid In,
   Withdrawn, Balance, Reason Type, Other Party Info…); a real Choice Feeds
   export should confirm it before go-live.
8. **Transaction Status for till receipts:** which shortcode is `PartyA`, and
   does the API initiator need the role on the store or each till?
9. **Buy Goods charges:** one charge line per receipt, or daily?

### 9.2 Not Safaricom

1. `docs/ke_etims/SPEC.md` is not in the repository on any branch, and there
   is no eTIMS module yet. `x_ke_mpesa_etims` therefore publishes the
   contract: `account.journal.ke_etims_payment_type` (KRA `pmtTyCd`: 01 cash
   … 06 mobile money, 07 other), set to `06` on every M-Pesa journal, plus
   `account.move._ke_etims_payment_type()`. When the eTIMS transmitter exists,
   it reads these, or the bridge is re-pointed to its fields.
2. `ke_sms_gateway` does not exist yet. `x_ke_mpesa_sms` sends through Odoo's
   SMS framework (`sms.template`), which is where a Kenyan gateway plugs in as
   the SMS provider.
3. Parked receipts stay on the M-Pesa suspense account (the journals'
   suspense account) with a reason, out of the work queue, until allocated.
   Say if parked money should instead move to a separate account.
4. Synad's unit reference: `res.partner.mpesa_account_ref`. If rentals keep
   units on another model, matching rule 3 needs one more lookup.

---

## 10. Tests and go-live

* A fake Daraja server (`x_ke_mpesa/tests/fake_daraja.py`): a real HTTP
  server on localhost (Odoo's test harness only lets localhost through) for
  token, STK, STK Query, C2B register and simulate, Pull and Transaction
  Status, whose outbound callbacks can be dropped, delayed, duplicated and
  reordered.
* Unit tests: STK password, phone normalisation, whole-shilling rounding,
  idempotency, duplicate callbacks, callback before response, every result
  code, each matching rule, POS session group reconciliation, de-duplication
  across C2B / Pull / file import, record rules.
* `tools/mpesa_sandbox.py`: STK on 174379 with the public sandbox passkey and
  C2B simulate on a sandbox test shortcode (run from a machine that can reach
  Safaricom; this build environment cannot).
* README go-live checklist per till.

All tests run in the official `odoo:19.0` image (19.0-20261015) against
PostgreSQL 16, with the OCA 19.0 branches on the addons path.

## 11. Milestones

* **M1** `x_ke_mpesa` core: models, Daraja client, controllers, statement
  lines, matching, invoice STK, reports 1–4, 6, 9.
* **M2** `x_ke_mpesa_pos`: terminal, paid-to-till picker, manual entry,
  session reconciliation, reports 5 and 7.
* **M3** `x_ke_mpesa_statement`: Pull provider, Org Portal mapping, charges and
  sweeps, report 8.
* **M4** `x_ke_mpesa_payment`: portal and website provider.
* **M5** bridges (`_etims`, `_sms`, `_mis`), then phase 2 `x_ke_mpesa_b2c`.
