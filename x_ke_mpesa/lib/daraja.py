# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Safaricom Daraja client.

Plain Python on purpose: no Odoo import, so the payment rules below can be
read, and tested, without a database. The Odoo side (``mpesa.app``) supplies
the token cache and turns the answers into records.

Ported from Fleet's ``organization/mpesa.py``, keeping what production taught
it and dropping what it got wrong:

* Only idempotent verbs are retried on read and status errors. A read error on
  the STK Push POST ("Connection reset by peer" was the production case)
  happens *after* Safaricom received the request, so a retry prompts the
  customer twice. Pure connection errors, where the request provably never
  left, are still retried for every verb: urllib3 does that regardless of
  ``allowed_methods``.
* "We don't know" and "Safaricom said no" are different exceptions:
  :class:`DarajaTransportError` and :class:`DarajaError`.
* Fleet fetched a new OAuth token before every call. Daraja's FAQ says each
  token request invalidates the previous one, so two workers kept knocking
  each other's token out (``404.001.03 Invalid Access Token``). The token now
  comes from a provider the caller owns, and a rejected token is refreshed once,
  never in a loop.
* Timestamps are Nairobi time. ``datetime.now()`` on a UTC server is not.
"""

import base64
import json
import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import requests
import urllib3.exceptions
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_logger = logging.getLogger(__name__)

# Kenya has kept UTC+3 without daylight saving since 1960. A fixed offset is
# exact and needs no time zone database.
NAIROBI = timezone(timedelta(hours=3), "EAT")

SANDBOX_URL = "https://sandbox.safaricom.co.ke"
PRODUCTION_URL = "https://api.safaricom.co.ke"

PATHS = {
    "token": "/oauth/v1/generate",
    "stk_push": "/mpesa/stkpush/v1/processrequest",
    "stk_query": "/mpesa/stkpushquery/v1/query",
    "c2b_register": "/mpesa/c2b/v2/registerurl",
    "c2b_simulate": "/mpesa/c2b/v2/simulate",
    "transaction_status": "/mpesa/transactionstatus/v1/query",
    "account_balance": "/mpesa/accountbalance/v1/query",
    "reversal": "/mpesa/reversal/v1/request",
    # B2C v1 (paymentrequest without a merchant OriginatorConversationID) was
    # retired after 26 October 2025; v3 takes our own idempotency key.
    "b2c": "/mpesa/b2c/v3/paymentrequest",
    "b2b": "/mpesa/b2b/v1/paymentrequest",
    "pull_register": "/pulltransactions/v1/register",
    "pull_query": "/pulltransactions/v1/query",
}

# Seconds. A read timeout on a POST means "unknown", never "failed".
TIMEOUT = 30

# Error codes that mean "this token is no good": refresh once, then retry.
# 404.001.03 (M-PESA Express), 400.003.01 (C2B, Transaction Status), 401.001
# (Pull) all read "Invalid Access Token".
TOKEN_ERRORS = frozenset({"404.001.03", "400.003.01", "401.001"})
# Error codes that mean "not now": nothing was carried out, try again later.
# 500.003.02 is both "System is busy" and "Spike Arrest Violation".
BUSY_ERRORS = frozenset({"500.003.02", "500.003.03"})

# Keys whose values never reach a log line.
SECRET_KEYS = frozenset({
    "Password", "SecurityCredential", "Authorization", "access_token",
    "consumer_secret", "passkey", "InitiatorPassword",
})
PHONE_KEYS = frozenset({"PartyA", "PhoneNumber", "Msisdn", "MSISDN", "PartyB",
                        "NominatedNumber", "Requester"})

# Daraja's own words for "STK Query: not finished yet".
_PENDING_RE = re.compile(r"being processed|still under processing", re.I)


class DarajaError(Exception):
    """Safaricom answered, and the answer was no.

    The request was not carried out, so the caller may safely try again (later
    if :attr:`busy`).
    """

    def __init__(self, message, code=None, http_status=None, request_id=None,
                 payload=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.request_id = request_id
        self.payload = payload or {}

    @property
    def token_invalid(self):
        return self.code in TOKEN_ERRORS

    @property
    def busy(self):
        return self.code in BUSY_ERRORS or bool(
            re.search(r"spike arrest|quota violation|system is busy",
                      self.message or "", re.I))

    @property
    def subscriber_locked(self):
        """Daraja's "a transaction is already in process for the subscriber"."""
        return bool(re.search(r"unable to lock subscriber", self.message or "", re.I))

    def __str__(self):
        if self.code:
            return f"{self.code}: {self.message}"
        return self.message


class DarajaTransportError(Exception):
    """We could not tell whether Safaricom carried the request out.

    ``sent`` is False only when the request provably never left (connection
    refused, DNS); then it is as safe to retry as a :class:`DarajaError`.
    """

    def __init__(self, message, sent=True):
        super().__init__(message)
        self.sent = sent


# --------------------------------------------------------------------------
# Small rules
# --------------------------------------------------------------------------

def normalize_msisdn(value):
    """A Kenyan mobile number as Daraja wants it: ``2547XXXXXXXX`` or ``2541XXXXXXXX``.

    Accepts 07…, 01…, 7…, 1…, 2547…, +254 7… with any spacing. Raises
    ValueError for anything else, including landlines and foreign numbers,
    which M-PESA would reject anyway.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 10 and digits[0] == "0" and digits[1] in "71":
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        digits = "254" + digits
    if len(digits) == 12 and digits[:4] in ("2547", "2541"):
        return digits
    raise ValueError(f"Not a Kenyan mobile number: {value!r}")


def mask_msisdn(value):
    """``2547*****149``: first four and last three digits, for anything we keep or show."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 8:
        return digits and "*" * len(digits)
    return digits[:4] + "*" * (len(digits) - 7) + digits[-3:]


def daraja_timestamp(moment=None):
    """``YYYYMMDDHHMMSS`` in Nairobi time."""
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(NAIROBI).strftime("%Y%m%d%H%M%S")


def stk_password(shortcode, passkey, timestamp):
    """Base64(BusinessShortCode + Passkey + Timestamp), Daraja's STK password."""
    raw = f"{shortcode}{passkey}{timestamp}".encode()
    return base64.b64encode(raw).decode()


def parse_daraja_time(value, assume=NAIROBI):
    """Any Daraja time as an aware UTC datetime, or None.

    Daraja writes times four ways: ``20191219102115`` (STK, as a number or a
    string), ``20231121121325`` (C2B TransTime), ``2020-08-05T10:13:00Z``
    (Pull) and ``2019-07-31 20:35:21``. Times without a zone are Nairobi time
    unless ``assume`` says otherwise.
    """
    if value in (None, "", False):
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d{14}", text):
            moment = datetime.strptime(text, "%Y%m%d%H%M%S")
        else:
            text = text.replace("Z", "+00:00")
            try:
                moment = datetime.fromisoformat(text)
            except ValueError:
                return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=assume)
    return moment.astimezone(UTC)


def clean_receipt(value):
    """An M-PESA receipt number, upper case, letters and digits only."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def whole_shillings(amount):
    """The amount as a whole number of shillings, half up. Never decimals."""
    try:
        value = Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Not an amount: {amount!r}") from exc
    return int(value)


def parse_amount(value):
    """Daraja amounts arrive as 10, "10", "5.00" or 1.0. Returns a float or 0.0."""
    if value in (None, ""):
        return 0.0
    try:
        return float(Decimal(str(value).replace(",", "")))
    except (InvalidOperation, ValueError):
        return 0.0


def account_reference(text, fallback="PAYMENT"):
    """Alphanumeric, at most 12 characters: shown to the customer in the prompt."""
    clean = re.sub(r"[^A-Za-z0-9]", "", str(text or ""))
    return clean[:12] or fallback


def transaction_desc(text, fallback="Payment"):
    """At most 13 characters of letters, digits and spaces."""
    clean = re.sub(r"[^A-Za-z0-9 ]", "", str(text or "")).strip()
    return clean[:13] or fallback


def redact(data):
    """A copy of ``data`` that is safe to log: secrets starred, phones masked."""
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if key in SECRET_KEYS:
                out[key] = "***"
            elif key in PHONE_KEYS and value not in (None, ""):
                out[key] = mask_msisdn(value)
            else:
                out[key] = redact(value)
        return out
    if isinstance(data, list):
        return [redact(item) for item in data]
    return data


def stk_metadata(callback):
    """``CallbackMetadata.Item`` as a dict, or ``{}`` (failures carry none)."""
    items = (callback.get("CallbackMetadata") or {}).get("Item") or []
    return {item.get("Name"): item.get("Value") for item in items if item.get("Name")}


def result_parameters(result):
    """``Result.ResultParameters.ResultParameter`` as a dict (async results)."""
    params = (result.get("ResultParameters") or {}).get("ResultParameter") or []
    if isinstance(params, dict):
        params = [params]
    return {item.get("Key"): item.get("Value") for item in params if item.get("Key")}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"})

# urllib3 errors raised before a single byte of the request went out
# (NameResolutionError is a NewConnectionError).
_NOT_SENT = (
    urllib3.exceptions.NewConnectionError,
    urllib3.exceptions.ConnectTimeoutError,
)


def was_sent(exc):
    """False when ``exc`` proves the request never reached Safaricom.

    A refused connection, a failed DNS lookup, a connect timeout or a failed
    TLS handshake: urllib3 has already retried those, and Daraja never saw the
    request. Anything later (a reset or timeout while reading the answer) may
    have been carried out.
    """
    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.SSLError)):
        return False
    reason = exc.args[0] if exc.args else None
    if isinstance(reason, urllib3.exceptions.MaxRetryError):
        reason = reason.reason
    return not isinstance(reason, _NOT_SENT)


def build_session():
    """A Session that retries what is safe to retry, and nothing else.

    The documented Requests pattern (HTTPAdapter + urllib3 Retry). POST is not
    in ``allowed_methods``: no read or status retries for it, only the
    connection retries urllib3 makes for every verb.
    """
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=_IDEMPOTENT_METHODS,
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


class DarajaClient:
    """One Daraja app: a base URL, a key pair, and somewhere to keep a token.

    ``token_provider(force=False, rejected=None)`` returns a bearer token. It
    is called with ``force=True`` and the rejected token after Daraja refuses
    one, and must then return a fresh token unless somebody else already
    replaced the rejected one. :meth:`fetch_token` is what it calls to get one.
    """

    def __init__(self, base_url, consumer_key, consumer_secret,
                 token_provider=None, session=None, timeout=TIMEOUT):
        self.base_url = (base_url or SANDBOX_URL).rstrip("/")
        self.consumer_key = consumer_key or ""
        self.consumer_secret = consumer_secret or ""
        self.session = session or SESSION
        self.timeout = timeout
        self._token = None
        self.token_provider = token_provider or self._memory_token

    # -- token --------------------------------------------------------------

    def _memory_token(self, force=False, rejected=None):
        if force or not self._token:
            self._token = self.fetch_token()[0]
        return self._token

    def fetch_token(self):
        """Ask Daraja for a new token: ``(token, expires_in_seconds)``.

        A GET, so it is retried on transient failures. Each call invalidates
        the previous token; call it only from a token provider.
        """
        credentials = base64.b64encode(
            f"{self.consumer_key}:{self.consumer_secret}".encode()).decode()
        url = self.base_url + PATHS["token"]
        try:
            response = self.session.get(
                url,
                params={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {credentials}"},
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            _logger.warning("Daraja token request failed: %s", exc)
            raise DarajaTransportError(
                "M-PESA could not be reached to authenticate.", sent=False) from exc
        data = self._decode(response)
        token = data.get("access_token")
        if response.status_code >= 400 or not token:
            raise self._error(response, data, "M-PESA refused the app credentials.")
        try:
            expires_in = int(data.get("expires_in") or 3599)
        except (TypeError, ValueError):
            expires_in = 3599
        return token, expires_in

    # -- transport ----------------------------------------------------------

    @staticmethod
    def _decode(response):
        try:
            data = response.json()
        except ValueError:
            return {"errorMessage": (response.text or "").strip()[:500]
                    or f"HTTP {response.status_code}"}
        return data if isinstance(data, dict) else {"Response": data}

    @staticmethod
    def _error(response, data, default):
        code = data.get("errorCode") or data.get("ResponseCode") or data.get("ResultCode")
        message = (data.get("errorMessage") or data.get("ResponseDescription")
                   or data.get("ResultDesc") or default)
        return DarajaError(
            str(message), code=str(code) if code not in (None, "") else None,
            http_status=response.status_code, request_id=data.get("requestId"),
            payload=data)

    def post(self, endpoint, payload):
        """POST ``payload`` to a Daraja endpoint and return the decoded body.

        Raises :class:`DarajaError` when Daraja refuses the request and
        :class:`DarajaTransportError` when the outcome is unknown. A refused
        token is replaced once and the request sent again, which is safe: a
        request with a bad token was never carried out.
        """
        url = self.base_url + PATHS.get(endpoint, endpoint)
        token = self.token_provider()
        for attempt in (1, 2):
            _logger.debug("Daraja POST %s %s", endpoint, json.dumps(redact(payload)))
            try:
                response = self.session.post(
                    url, json=payload, timeout=self.timeout,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                )
            except requests.exceptions.ConnectionError as exc:
                sent = was_sent(exc)
                _logger.warning("Daraja %s: connection error (sent=%s): %s",
                                endpoint, sent, exc)
                if not sent:
                    raise DarajaTransportError(
                        "M-PESA could not be reached. Nothing was sent.",
                        sent=False) from exc
                raise DarajaTransportError(
                    "The connection to M-PESA dropped. The request may or may "
                    "not have gone through.", sent=True) from exc
            except requests.exceptions.Timeout as exc:
                _logger.warning("Daraja %s: timed out: %s", endpoint, exc)
                raise DarajaTransportError(
                    "M-PESA did not answer in time. The request may or may "
                    "not have gone through.", sent=True) from exc
            except requests.exceptions.RequestException as exc:
                _logger.warning("Daraja %s failed: %s", endpoint, exc)
                raise DarajaTransportError(str(exc), sent=True) from exc
            data = self._decode(response)
            _logger.debug("Daraja %s answered %s %s", endpoint,
                          response.status_code, json.dumps(redact(data)))
            failed = response.status_code >= 400 or "errorCode" in data
            if not failed:
                return data
            error = self._error(response, data, f"M-PESA refused the {endpoint} request.")
            if error.token_invalid and attempt == 1:
                token = self.token_provider(force=True, rejected=token)
                continue
            raise error
        raise AssertionError("unreachable")  # pragma: no cover

    # -- M-PESA Express -----------------------------------------------------

    def stk_push(self, *, business_shortcode, passkey, amount, phone, party_b,
                 transaction_type, callback_url, reference, description,
                 timestamp=None):
        timestamp = timestamp or daraja_timestamp()
        msisdn = normalize_msisdn(phone)
        payload = {
            "BusinessShortCode": str(business_shortcode),
            "Password": stk_password(business_shortcode, passkey, timestamp),
            "Timestamp": timestamp,
            "TransactionType": transaction_type,
            "Amount": whole_shillings(amount),
            "PartyA": msisdn,
            "PartyB": str(party_b),
            "PhoneNumber": msisdn,
            "CallBackURL": callback_url,
            "AccountReference": account_reference(reference),
            "TransactionDesc": transaction_desc(description),
        }
        data = self.post("stk_push", payload)
        if str(data.get("ResponseCode", "0")) != "0":
            raise DarajaError(
                data.get("ResponseDescription") or "M-PESA refused the prompt.",
                code=str(data.get("ResponseCode")), payload=data)
        return data

    def stk_query(self, *, business_shortcode, passkey, checkout_request_id,
                  timestamp=None):
        """The status of an STK Push. ``{"pending": True}`` while unfinished.

        Otherwise the body, whose ``ResultCode`` is authoritative. There is no
        receipt number in it: Daraja only gives that in the callback.
        """
        timestamp = timestamp or daraja_timestamp()
        payload = {
            "BusinessShortCode": str(business_shortcode),
            "Password": stk_password(business_shortcode, passkey, timestamp),
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }
        try:
            data = self.post("stk_query", payload)
        except DarajaError as error:
            if _PENDING_RE.search(error.message or ""):
                return {"pending": True, "ResultDesc": error.message}
            raise
        data.setdefault("pending", False)
        return data

    # -- C2B ----------------------------------------------------------------

    def c2b_register(self, *, shortcode, confirmation_url, validation_url,
                     response_type="Completed"):
        return self.post("c2b_register", {
            "ShortCode": str(shortcode),
            "ResponseType": response_type,
            "ConfirmationURL": confirmation_url,
            "ValidationURL": validation_url,
        })

    def c2b_simulate(self, *, shortcode, command_id, amount, msisdn, bill_ref=None):
        """Sandbox only: make Daraja post a confirmation as if a customer paid."""
        return self.post("c2b_simulate", {
            "ShortCode": str(shortcode),
            "CommandID": command_id,
            "Amount": whole_shillings(amount),
            "Msisdn": normalize_msisdn(msisdn),
            "BillRefNumber": bill_ref if bill_ref is not None else "",
        })

    # -- Initiator APIs (async: the answer comes to ResultURL) ----------------

    def transaction_status(self, *, initiator, security_credential, transaction_id,
                           party_a, result_url, timeout_url, identifier_type="4",
                           remarks="Status", occasion="", original_conversation_id=None):
        payload = {
            "Initiator": initiator,
            "SecurityCredential": security_credential,
            "CommandID": "TransactionStatusQuery",
            "TransactionID": transaction_id,
            "PartyA": str(party_a),
            "IdentifierType": str(identifier_type),
            "ResultURL": result_url,
            "QueueTimeOutURL": timeout_url,
            "Remarks": (remarks or "Status")[:100],
            "Occasion": (occasion or "")[:100],
        }
        if original_conversation_id:
            payload["OriginalConversationID"] = original_conversation_id
        return self.post("transaction_status", payload)

    def account_balance(self, *, initiator, security_credential, party_a,
                        result_url, timeout_url, identifier_type="4", remarks="Balance"):
        return self.post("account_balance", {
            "Initiator": initiator,
            "SecurityCredential": security_credential,
            "CommandID": "AccountBalance",
            "PartyA": str(party_a),
            "IdentifierType": str(identifier_type),
            "Remarks": (remarks or "Balance")[:100],
            "QueueTimeOutURL": timeout_url,
            "ResultURL": result_url,
        })

    def reversal(self, *, initiator, security_credential, transaction_id, amount,
                 receiver_party, result_url, timeout_url, remarks="Reversal",
                 occasion=""):
        return self.post("reversal", {
            "Initiator": initiator,
            "SecurityCredential": security_credential,
            "CommandID": "TransactionReversal",
            "TransactionID": transaction_id,
            "Amount": whole_shillings(amount),
            "ReceiverParty": str(receiver_party),
            # Daraja's spelling.
            "RecieverIdentifierType": "11",
            "ResultURL": result_url,
            "QueueTimeOutURL": timeout_url,
            "Remarks": (remarks or "Reversal")[:100],
            "Occasion": (occasion or "")[:100],
        })

    def b2c(self, *, originator_conversation_id, initiator, security_credential,
            command_id, amount, party_a, phone, result_url, timeout_url,
            remarks="Refund", occasion=""):
        return self.post("b2c", {
            "OriginatorConversationID": originator_conversation_id,
            "InitiatorName": initiator,
            "SecurityCredential": security_credential,
            "CommandID": command_id,
            "Amount": whole_shillings(amount),
            "PartyA": str(party_a),
            "PartyB": normalize_msisdn(phone),
            "Remarks": (remarks or "Refund")[:100],
            "QueueTimeOutURL": timeout_url,
            "ResultURL": result_url,
            # Daraja's spelling.
            "Occassion": (occasion or "")[:100],
        })

    def b2b(self, *, initiator, security_credential, command_id, amount, party_a,
            party_b, account_ref, result_url, timeout_url, remarks="Payment",
            requester=None):
        payload = {
            "Initiator": initiator,
            "SecurityCredential": security_credential,
            "CommandID": command_id,
            "SenderIdentifierType": "4",
            "RecieverIdentifierType": "4",
            "Amount": whole_shillings(amount),
            "PartyA": str(party_a),
            "PartyB": str(party_b),
            "AccountReference": (account_ref or "")[:13],
            "Remarks": (remarks or "Payment")[:100],
            "QueueTimeOutURL": timeout_url,
            "ResultURL": result_url,
        }
        if requester:
            payload["Requester"] = normalize_msisdn(requester)
        return self.post("b2b", payload)

    # -- Pull ---------------------------------------------------------------

    def pull_register(self, *, shortcode, nominated_number, callback_url):
        return self.post("pull_register", {
            "ShortCode": str(shortcode),
            "RequestType": "Pull",
            "NominatedNumber": nominated_number,
            "CallBackURL": callback_url,
        })

    def pull_query(self, *, shortcode, start, end, offset=0):
        """One page of C2B transactions between ``start`` and ``end`` (aware datetimes).

        Returns the list of rows (dicts). Daraja nests them as
        ``Response: [[...]]`` and says "no transactions" with ResponseCode 1001.
        """
        try:
            data = self.post("pull_query", {
                "ShortCode": str(shortcode),
                "StartDate": start.astimezone(NAIROBI).strftime("%Y-%m-%d %H:%M:%S"),
                "EndDate": end.astimezone(NAIROBI).strftime("%Y-%m-%d %H:%M:%S"),
                "OffSetValue": str(int(offset)),
            })
        except DarajaError as error:
            # "Failed to retrieve transactions. The short code does not have
            # any available transactions." is an empty page, not a failure.
            if re.search(r"not have any available transactions", error.message, re.I):
                return []
            raise
        if str(data.get("ResponseCode")) == "1001":
            return []
        rows = data.get("Response")
        if rows is None:
            rows = data.get("Transaction") or []
        if isinstance(rows, str):
            try:
                rows = json.loads(rows)
            except ValueError:
                rows = []
        flat = []
        stack = [rows]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(reversed(item))
            elif isinstance(item, dict) and item.get("transactionId"):
                flat.append(item)
        return flat


# --------------------------------------------------------------------------
# STK result codes
# --------------------------------------------------------------------------

# Daraja result code -> what the cashier is told. Anything else falls back to
# Daraja's own ResultDesc.
STK_RESULTS = {
    "0": "Paid.",
    "1": "The customer does not have enough money on M-PESA.",
    "2": "The amount is below the M-PESA minimum.",
    "3": "The amount is above the M-PESA maximum for one payment.",
    "4": "The customer has reached their daily M-PESA limit.",
    "8": "This till cannot take more money now: its balance limit is reached.",
    "17": "The same amount was asked of this customer less than 2 minutes ago. "
          "Wait 2 minutes, or ask them to pay the till directly.",
    "1001": "The customer's phone is busy with another M-PESA session. Ask "
            "them to close it and try again.",
    "1019": "The prompt expired before the customer entered their PIN.",
    "1025": "M-PESA could not send the prompt. Try again.",
    "1032": "The customer cancelled the prompt.",
    "1037": "The customer's phone could not be reached. Check it is on and "
            "has signal, then try again.",
    "2001": "Wrong M-PESA PIN. Ask the customer to try again.",
    "2028": "This till is not set up for this kind of payment. Check the "
            "STK settings of the shortcode.",
    "8006": "The customer's M-PESA PIN is locked. They should call 100 or 200.",
    "SFC_IC0003": "This till is not set up for this kind of payment. Check "
                  "the STK settings of the shortcode.",
}


def stk_message(code, fallback=""):
    return STK_RESULTS.get(str(code), fallback or f"M-PESA result {code}.")
