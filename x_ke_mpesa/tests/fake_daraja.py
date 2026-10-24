# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""A Daraja that lives on 127.0.0.1 for the length of a test class.

Real HTTP, so the client's retry rules, timeouts and error handling are the
ones under test (Odoo's test harness lets localhost through and nothing else).
What Safaricom would post back is not sent straight away: it waits in
:attr:`FakeDaraja.outbox`, and the test decides what happens to it: deliver
it, drop it, deliver it twice, deliver it late, deliver it out of order.

Behaviour copied from Daraja itself, not idealised:

* each token request invalidates the previous token (``404.001.03``);
* the STK password must be base64(BusinessShortCode + passkey + Timestamp);
* amounts must be whole numbers;
* failure callbacks carry no ``CallbackMetadata``;
* STK Query answers ``500.001.1001 The transaction is being processed``
  until the outcome is known, and never returns a receipt number;
* production C2B registration is one-time.
"""

import base64
import itertools
import json
import random
import string
import threading
from datetime import UTC, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

# The public sandbox passkey of shortcode 174379, published on the Daraja portal.
SANDBOX_PASSKEY = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
NAIROBI = timezone(timedelta(hours=3))


def receipt_number():
    return "T" + "".join(random.choices(string.ascii_uppercase + string.digits, k=9))


def nairobi_now():
    return datetime.now(UTC).astimezone(NAIROBI).strftime("%Y%m%d%H%M%S")


class FakeDaraja:
    def __init__(self):
        self.lock = threading.RLock()
        self.counter = itertools.count(1)
        self.consumer = ("ck_test", "cs_test")
        self.passkeys = {"174379": SANDBOX_PASSKEY}
        self.tokens = []
        self.invalidate_previous_tokens = True
        self.reject_all_tokens = False
        self.token_calls = 0
        self.requests = []          # (path, body) as received
        self.outbox = []            # [{"url": ..., "body": ...}]
        self.next_stk = []          # scripted outcomes, consumed in order
        self.stk = {}               # CheckoutRequestID -> state
        self.c2b_urls = {}          # shortcode -> {"confirmation": ..., "validation": ...}
        self.production = False     # one-time C2B registration when True
        self.receipts = {}          # receipt -> {"amount": ..., "shortcode": ...}
        self.pull_rows = []
        self.pull_page_size = 2
        self.fail_next = {}         # path -> (status, body), one call each
        self.drop_after_send = set()  # paths: carry the request out, then hang up
        self.server = None
        self.thread = None

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake._handle(self, "GET")

            def do_POST(self):
                fake._handle(self, "POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def reset(self):
        with self.lock:
            self.requests.clear()
            self.outbox.clear()
            self.next_stk.clear()
            self.stk.clear()
            self.fail_next.clear()
            self.drop_after_send.clear()
            self.pull_rows.clear()
            self.receipts.clear()
            self.c2b_urls.clear()
            self.production = False

    # -- scripting ------------------------------------------------------------

    def script_stk(self, result_code=0, receipt=None, amount=None, phone=None,
                   desc=None, query="final"):
        """The outcome of the next STK Push. ``query`` is what STK Query says:
        "final" (the outcome), "pending" (still processing) or a dict."""
        self.next_stk.append({"result_code": result_code, "receipt": receipt,
                              "amount": amount, "phone": phone, "desc": desc,
                              "query": query})

    def c2b_payment(self, shortcode, amount, receipt=None, bill_ref="", business_shortcode=None,
                    first_name="JOHN", msisdn="2547 ***** 149", trans_time=None,
                    org_balance=None, transaction_type=None):
        """A customer pays ``shortcode``: queue the confirmation Daraja would post."""
        receipt = receipt or receipt_number()
        urls = self.c2b_urls.get(str(shortcode))
        if not urls:
            raise AssertionError(f"No C2B URL registered for {shortcode}")
        body = {
            "TransactionType": transaction_type or ("Pay Bill" if bill_ref else "Buy Goods"),
            "TransID": receipt,
            "TransTime": trans_time or nairobi_now(),
            "TransAmount": f"{amount:.2f}" if isinstance(amount, float) else str(amount),
            "BusinessShortCode": str(business_shortcode or shortcode),
            "BillRefNumber": bill_ref,
            "InvoiceNumber": "",
            "OrgAccountBalance": "" if org_balance is None else f"{org_balance:.2f}",
            "ThirdPartyTransID": "",
            "MSISDN": msisdn,
            "FirstName": first_name,
            "MiddleName": "",
            "LastName": "",
        }
        with self.lock:
            self.receipts[receipt] = {"amount": amount, "shortcode": str(shortcode)}
            self.outbox.append({"url": urls["confirmation"], "body": body})
        return receipt

    # -- what the test does with callbacks ------------------------------------

    def take(self):
        """All pending callbacks, removed from the outbox."""
        with self.lock:
            items, self.outbox = self.outbox, []
        return items

    def deliver(self, send, *, drop=(), duplicate=(), reverse=False):
        """Send pending callbacks through ``send(url, body)``.

        ``drop`` and ``duplicate`` are positions in the outbox; ``reverse``
        delivers newest first. Returns what was sent.
        """
        items = self.take()
        order = list(range(len(items)))
        if reverse:
            order.reverse()
        sent = []
        for index in order:
            if index in drop:
                continue
            copies = 2 if index in duplicate else 1
            for _copy in range(copies):
                send(items[index]["url"], items[index]["body"])
                sent.append(items[index])
        return sent

    # -- HTTP -----------------------------------------------------------------

    def _reply(self, handler, status, body):
        data = json.dumps(body).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _handle(self, handler, method):
        parts = urlsplit(handler.path)
        path = parts.path
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        with self.lock:
            self.requests.append((path, body))
            failure = self.fail_next.pop(path, None)
        if failure:
            return self._reply(handler, *failure)
        if path == "/oauth/v1/generate":
            return self._token(handler, parse_qs(parts.query))
        if not self._authorised(handler):
            return self._reply(handler, 404, {
                "requestId": "fake", "errorCode": "404.001.03",
                "errorMessage": "Invalid Access Token"})
        route = {
            "/mpesa/stkpush/v1/processrequest": self._stk_push,
            "/mpesa/stkpushquery/v1/query": self._stk_query,
            "/mpesa/c2b/v2/registerurl": self._c2b_register,
            "/mpesa/c2b/v2/simulate": self._c2b_simulate,
            "/mpesa/transactionstatus/v1/query": self._transaction_status,
            "/pulltransactions/v1/register": self._pull_register,
            "/pulltransactions/v1/query": self._pull_query,
            "/mpesa/b2c/v3/paymentrequest": self._async_accepted,
            "/mpesa/reversal/v1/request": self._async_accepted,
            "/mpesa/b2b/v1/paymentrequest": self._async_accepted,
            "/mpesa/accountbalance/v1/query": self._async_accepted,
        }.get(path)
        if not route:
            return self._reply(handler, 404, {"errorCode": "404.001.01",
                                              "errorMessage": "Resource not found"})
        status, answer = route(body)
        if path in self.drop_after_send:
            self.drop_after_send.discard(path)
            # Carried out, but the answer never arrives: a reset after send.
            handler.close_connection = True
            handler.connection.shutdown(2)
            return None
        return self._reply(handler, status, answer)

    def _token(self, handler, query):
        expected = base64.b64encode(":".join(self.consumer).encode()).decode()
        if handler.headers.get("Authorization") != f"Basic {expected}" or \
                query.get("grant_type") != ["client_credentials"]:
            return self._reply(handler, 400, {"errorCode": "400.008.01",
                                              "errorMessage": "Invalid Authentication passed"})
        with self.lock:
            self.token_calls += 1
            token = f"tok{next(self.counter)}"
            if self.invalidate_previous_tokens:
                self.tokens = [token]
            else:
                self.tokens.append(token)
        return self._reply(handler, 200, {"access_token": token, "expires_in": "3599"})

    def _authorised(self, handler):
        if self.reject_all_tokens:
            return False
        auth = handler.headers.get("Authorization") or ""
        return auth.startswith("Bearer ") and auth[7:] in self.tokens

    def _stk_push(self, body):
        bsc = str(body.get("BusinessShortCode"))
        passkey = self.passkeys.get(bsc)
        expected = base64.b64encode(f"{bsc}{passkey}{body.get('Timestamp')}".encode()).decode()
        if not passkey or body.get("Password") != expected:
            return 500, {"requestId": "fake", "errorCode": "500.001.1001",
                         "errorMessage": "Wrong credentials"}
        amount = body.get("Amount")
        if not isinstance(amount, int) or amount < 1:
            return 400, {"requestId": "fake", "errorCode": "400.002.02",
                         "errorMessage": "Bad Request - Invalid Amount"}
        n = next(self.counter)
        checkout = f"ws_CO_{n:08d}"
        merchant = f"{n}-fake-1"
        script = self.next_stk.pop(0) if self.next_stk else {"result_code": 0, "query": "final"}
        code = script["result_code"]
        receipt = script.get("receipt") or (receipt_number() if code == 0 else None)
        state = {"request": body, "script": script, "receipt": receipt,
                 "merchant": merchant, "code": code}
        callback = {"Body": {"stkCallback": {
            "MerchantRequestID": merchant,
            "CheckoutRequestID": checkout,
            "ResultCode": code,
            "ResultDesc": script.get("desc") or (
                "The service request is processed successfully." if code == 0
                else "Request cancelled by user"),
        }}}
        if code == 0:
            callback["Body"]["stkCallback"]["CallbackMetadata"] = {"Item": [
                {"Name": "Amount", "Value": float(script.get("amount") or amount)},
                {"Name": "MpesaReceiptNumber", "Value": receipt},
                {"Name": "TransactionDate", "Value": int(nairobi_now())},
                {"Name": "PhoneNumber", "Value": int(script.get("phone") or body["PhoneNumber"])},
            ]}
            self.receipts[receipt] = {"amount": amount, "shortcode": str(body.get("PartyB"))}
        with self.lock:
            self.stk[checkout] = state
            self.outbox.append({"url": body["CallBackURL"], "body": callback})
        return 200, {
            "MerchantRequestID": merchant,
            "CheckoutRequestID": checkout,
            "ResponseCode": "0",
            "ResponseDescription": "Success. Request accepted for processing",
            "CustomerMessage": "Success. Request accepted for processing",
        }

    def _stk_query(self, body):
        state = self.stk.get(body.get("CheckoutRequestID"))
        if not state:
            return 500, {"requestId": "fake", "errorCode": "500.001.1001",
                         "errorMessage": "Invalid CheckoutRequestID"}
        query = state["script"].get("query", "final")
        if query == "pending":
            return 500, {"requestId": "fake", "errorCode": "500.001.1001",
                         "errorMessage": "The transaction is being processed"}
        if isinstance(query, dict):
            return 200, query
        return 200, {
            "ResponseCode": "0",
            "ResponseDescription": "The service request has been accepted successsfully",
            "MerchantRequestID": state["merchant"],
            "CheckoutRequestID": body.get("CheckoutRequestID"),
            "ResultCode": str(state["code"]),
            "ResultDesc": "The service request is processed successfully."
            if state["code"] == 0 else "Request cancelled by user",
        }

    def _c2b_register(self, body):
        shortcode = str(body.get("ShortCode"))
        if self.production and shortcode in self.c2b_urls:
            return 500, {"requestId": "fake", "errorCode": "500.003.1001",
                         "errorMessage": "Urls are already registered"}
        if body.get("ResponseType") not in ("Completed", "Cancelled"):
            return 400, {"errorCode": "400.002.02", "errorMessage": "Invalid ResponseType"}
        self.c2b_urls[shortcode] = {"confirmation": body.get("ConfirmationURL"),
                                    "validation": body.get("ValidationURL")}
        return 200, {"OriginatorCoversationID": "fake-reg", "ResponseCode": "0",
                     "ResponseDescription": "Success"}

    def _c2b_simulate(self, body):
        shortcode = str(body.get("ShortCode"))
        if shortcode not in self.c2b_urls:
            return 500, {"errorCode": "500.003.1001", "errorMessage": "No URL registered"}
        self.c2b_payment(shortcode, body.get("Amount"), bill_ref=body.get("BillRefNumber") or "")
        return 200, {"OriginatorCoversationID": "fake-sim", "ResponseCode": "0",
                     "ResponseDescription": "Accept the service request successfully."}

    def _transaction_status(self, body):
        receipt = body.get("TransactionID")
        known = self.receipts.get(receipt)
        conversation = f"AG_fake_{next(self.counter)}"
        if known:
            result = {"Result": {
                "ResultType": 0, "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "OriginatorConversationID": "fake-ts", "ConversationID": conversation,
                "TransactionID": receipt,
                "ResultParameters": {"ResultParameter": [
                    {"Key": "ReceiptNo", "Value": receipt},
                    {"Key": "Amount", "Value": str(known["amount"])},
                    {"Key": "TransactionStatus", "Value": "Completed"},
                    {"Key": "FinalisedTime", "Value": nairobi_now()},
                ]},
            }}
        else:
            result = {"Result": {"ResultType": 0, "ResultCode": 2001,
                                 "ResultDesc": "The initiator information is invalid.",
                                 "ConversationID": conversation, "TransactionID": receipt}}
        with self.lock:
            self.outbox.append({"url": body.get("ResultURL"), "body": result})
        return 200, {"OriginatorConversationID": "fake-ts", "ConversationID": conversation,
                     "ResponseCode": "0",
                     "ResponseDescription": "Accept the service request successfully."}

    def _pull_register(self, body):
        return 200, {"ResponseRefID": "fake-pull", "ResponseStatus": "1000",
                     "ShortCode": body.get("ShortCode"),
                     "ResponseDescription": "Shortcode Registered Successfully"}

    def _pull_query(self, body):
        offset = int(body.get("OffSetValue") or 0)
        rows = [row for row in self.pull_rows
                if str(row.get("_shortcode", body.get("ShortCode"))) == str(body.get("ShortCode"))]
        page = rows[offset:offset + self.pull_page_size]
        if not page:
            return 200, {"ResponseRefID": "fake", "ResponseCode": "1001",
                         "ResponseMessage": "No transactions", "Response": [[]]}
        clean = [{k: v for k, v in row.items() if not k.startswith("_")} for row in page]
        return 200, {"ResponseRefID": "fake", "ResponseCode": "1000",
                     "ResponseMessage": "Success", "Response": [clean]}

    def _async_accepted(self, body):
        conversation = f"AG_fake_{next(self.counter)}"
        return 200, {"OriginatorConversationID": body.get("OriginatorConversationID", "fake"),
                     "ConversationID": conversation, "ResponseCode": "0",
                     "ResponseDescription": "Accept the service request successfully."}
