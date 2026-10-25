# -*- coding: utf-8 -*-
"""The adapter contract every eTIMS backend implements, and the data it speaks.

Nothing in this package imports the ORM. An adapter is handed a device record
for its settings and a ``recorder`` to log each exchange through, and it talks
in the plain data classes below: a ``Sale`` in, a ``Receipt`` out. The models
build the one and store the other, so the queue, the invoice tab and the PDF
are the same whichever backend signed the invoice.

Errors carry what the queue needs to decide, not a story
--------------------------------------------------------
Three questions decide what happens to a document after a failure, and each
``EtimsError`` answers them as flags rather than as subclasses to match on:

``retryable``                Can the same request succeed later?
``may_have_been_processed``  Could the other side have recorded it already? If
                             so the retry must be the *same* request, with the
                             same invoice number, so a duplicate is refused
                             rather than signed twice.
``duplicate``                The other side says it already has this record.

Amounts
-------
eTIMS amounts include VAT. In KRA's own sample a taxable amount of 295,000 at
18% carries 45,000 of tax (295,000 x 18/118) and the total equals the taxable
amount, and the TIS specification's purchase sample agrees (630,000 carries
96,101.70). So ``taxable_amount`` below is VAT-inclusive and ``tax_amount`` is
the VAT inside it. The figures come from Odoo's own computation of the invoice,
so the receipt KRA signs is the invoice the customer was sent.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import requests

_logger = logging.getLogger(__name__)

#: Kenya keeps East Africa Time all year. KRA's timestamps are in it.
EAT = timezone(timedelta(hours=3), "EAT")

CENT = Decimal("0.01")

#: Keys whose values never reach a log, an error message or the chatter.
SECRET_KEYS = {
    "cmckey", "cmc_key", "x-api-key", "api_key", "apikey", "authorization",
    "token", "callback_token", "password", "secret",
}

#: The five KRA tax types, by code. Used only to lay out per-type totals,
#: which the wire format names by letter (taxblAmtA ... taxblAmtE). What each
#: letter means and its rate come from the synced KRA code list (class 04).
TAX_TYPE_LETTERS = ("A", "B", "C", "D", "E")


# --------------------------------------------------------------- helpers

def money(value):
    """Round to cents, half up, the way KRA's NUMBER(18,2) fields expect."""
    if value is None:
        return None
    if not isinstance(value, Decimal):
        value = Decimal(repr(value) if isinstance(value, float) else str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def json_number(value):
    """A Decimal as a JSON number: whole amounts stay integers, as in KRA's samples."""
    if value is None:
        return None
    value = money(value)
    return int(value) if value == value.to_integral_value() else float(value)


def kra_datetime(value):
    """yyyyMMddHHmmss in Kenyan time. Naive datetimes are taken to be UTC,
    which is what the ORM stores."""
    if not value:  # None, or an empty Datetime field's False
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(EAT).strftime("%Y%m%d%H%M%S")


def kra_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(EAT).date()
    return value.strftime("%Y%m%d")


def parse_kra_datetime(text):
    """KRA timestamps to naive UTC, for the ORM. None when empty or malformed:
    the specification's own samples carry dates such as "-1"."""
    if not text or not str(text).strip():
        return None
    text = str(text).strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            local = datetime.strptime(text[:19], fmt).replace(tzinfo=EAT)
        except ValueError:
            continue
        return local.astimezone(timezone.utc).replace(tzinfo=None)
    return None


def parse_kra_date(text):
    if not text or not str(text).strip():
        return None
    text = str(text).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def redact(value, secrets=()):
    """A copy of ``value`` with every secret removed.

    Two passes, because one is not enough: values under a secret-looking key
    are masked wherever they sit, and any known secret string is then scrubbed
    from whatever text is left, so a key echoed back inside an error message
    does not survive either.
    """
    secrets = [s for s in secrets if s and len(s) >= 4]

    def scrub(text):
        for secret in secrets:
            text = text.replace(secret, "***")
        return text

    def walk(node):
        if isinstance(node, dict):
            return {
                key: ("***" if str(key).strip().lower() in SECRET_KEYS and item
                      else walk(item))
                for key, item in node.items()
            }
        if isinstance(node, (list, tuple)):
            return [walk(item) for item in node]
        if isinstance(node, str):
            return scrub(node)
        return node

    return walk(value)


def first(data, *keys, default=None):
    """The first of ``keys`` present and not empty in ``data``. Responses
    differ in spelling between a specification's tables and its samples."""
    if not isinstance(data, dict):
        return default
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


# ---------------------------------------------------------------- errors

class EtimsError(Exception):
    """Anything that stopped a request. See the module docstring for the flags."""

    def __init__(self, message, *, code=None, retryable=False,
                 may_have_been_processed=False, duplicate=False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.may_have_been_processed = may_have_been_processed
        self.duplicate = duplicate

    def __str__(self):
        return "%s: %s" % (self.code, self.message) if self.code else self.message


class EtimsConfigError(EtimsError):
    """The device or the data is set up wrongly. Retrying will not help."""


class NotSupported(EtimsError):
    """The backend has no such operation."""

    def __init__(self, backend_label, operation):
        super().__init__(
            "%s is not supported by %s." % (operation, backend_label))
        self.operation = operation


# ------------------------------------------------------------------ data

@dataclass
class Line:
    """One line of a sale, credit note, purchase or stock movement."""

    seq: int
    item_code: str
    item_class_code: str
    name: str
    quantity: Decimal
    #: VAT included, before discount.
    unit_price: Decimal
    tax_type: str
    #: quantity x unit_price.
    supply_amount: Decimal
    discount_amount: Decimal
    #: VAT included, after discount. KRA's totAmt equals it.
    taxable_amount: Decimal
    tax_amount: Decimal
    discount_rate: Decimal = Decimal(0)
    packaging_unit: str = "NT"
    quantity_unit: str = "U"
    package_quantity: Decimal = None
    barcode: str = None
    expiry_date: date = None
    #: The provider's own id for the item, where it keeps one (DigiTax).
    provider_item_id: str = None
    #: For a purchase line: the supplier's item.
    supplier_item_code: str = None
    supplier_item_class_code: str = None
    supplier_item_name: str = None

    @property
    def total_amount(self):
        return self.taxable_amount


@dataclass
class Totals:
    taxable: dict
    tax: dict
    rates: dict

    @property
    def total_taxable(self):
        return sum(self.taxable.values(), Decimal(0))

    @property
    def total_tax(self):
        return sum(self.tax.values(), Decimal(0))

    @property
    def total_amount(self):
        return self.total_taxable


def summarize(lines, rates):
    """Per-type totals as sums of the already-rounded lines, so the header
    always agrees with the lines (KRA checks that it does)."""
    taxable = dict.fromkeys(TAX_TYPE_LETTERS, Decimal("0.00"))
    tax = dict.fromkeys(TAX_TYPE_LETTERS, Decimal("0.00"))
    for line in lines:
        taxable[line.tax_type] += money(line.taxable_amount)
        tax[line.tax_type] += money(line.tax_amount)
    return Totals(
        taxable=taxable, tax=tax,
        rates={letter: Decimal(str(rates.get(letter, 0) or 0))
               for letter in TAX_TYPE_LETTERS})


@dataclass
class Operator:
    """Who KRA records as having registered or changed the data."""

    id: str
    name: str


@dataclass
class Sale:
    """A sale or a credit note, ready to sign."""

    invoice_no: int
    trader_invoice_no: str
    lines: list
    confirmed_at: datetime
    sale_date: date
    tax_rates: dict
    operator: Operator
    #: "S" sale or "R" credit note (KRA code class 37).
    receipt_type: str = "S"
    #: "N" normal, "C" copy, "T" training, "P" proforma (code class 14).
    sales_type: str = "N"
    customer_pin: str = None
    customer_name: str = None
    customer_phone: str = None
    payment_type: str = None
    original_invoice_no: int = None
    #: The provider's id for the sale being credited (DigiTax).
    original_provider_ref: str = None
    reason_code: str = None
    refunded_at: datetime = None
    remark: str = None
    idempotency_key: str = None
    callback_url: str = None
    trade_name: str = None

    @property
    def is_credit_note(self):
        return self.receipt_type == "R"

    def totals(self):
        return summarize(self.lines, self.tax_rates)


@dataclass
class Receipt:
    """What every signing method returns, whichever backend signed."""

    #: signed, pending or failed.
    status: str
    kra_invoice_no: int = None
    receipt_no: int = None
    total_receipt_no: int = None
    internal_data: str = None
    signature: str = None
    scu_id: str = None
    mrc_no: str = None
    #: Naive UTC.
    signed_at: datetime = None
    qr_url: str = None
    provider_ref: str = None
    error: str = None
    raw: dict = field(default_factory=dict)


@dataclass
class DeviceInfo:
    """What initialising a device tells us about it."""

    tin: str = None
    bhf_id: str = None
    taxpayer_name: str = None
    branch_name: str = None
    scu_id: str = None
    mrc_no: str = None
    device_id: str = None
    #: Secret. Stored on the device, never logged.
    cmc_key: str = None
    raw: dict = field(default_factory=dict)


@dataclass
class Item:
    item_code: str
    item_class_code: str
    product_type: str
    name: str
    origin_country: str
    packaging_unit: str
    quantity_unit: str
    tax_type: str
    default_price: Decimal
    operator: Operator
    barcode: str = None
    active: bool = True
    provider_item_id: str = None
    callback_url: str = None


@dataclass
class Purchase:
    """A supplier's sale to us, confirmed as our purchase."""

    invoice_no: int
    lines: list
    purchase_date: date
    tax_rates: dict
    operator: Operator
    payment_type: str = "01"
    supplier_pin: str = None
    supplier_bhf_id: str = None
    supplier_name: str = None
    supplier_invoice_no: int = None
    #: "A" automatic (fetched from eTIMS) or "M" manual (code class 31).
    registration_type: str = "A"
    #: "P" purchase or "R" credit note (code class 38).
    receipt_type: str = "P"
    #: "02" approved, "04" cancelled... (code class 11).
    status: str = "02"
    confirmed_at: datetime = None
    provider_ref: str = None
    remark: str = None

    def totals(self):
        return summarize(self.lines, self.tax_rates)


@dataclass
class StockIO:
    """Stock in or out (KRA code class 12: 0x in, 1x out)."""

    sar_no: int
    io_type: str
    lines: list
    occurred_on: date
    operator: Operator
    original_sar_no: int = 0
    registration_type: str = "M"
    customer_pin: str = None
    customer_name: str = None
    customer_bhf_id: str = None
    remark: str = None


@dataclass
class StockMaster:
    item_code: str
    remaining_quantity: Decimal
    operator: Operator
    provider_item_id: str = None


@dataclass
class ImportUpdate:
    """Approve (3) or cancel (4) an import customs declared (code class 26)."""

    task_code: str
    declaration_date: date
    item_seq: int
    hs_code: str
    item_class_code: str
    item_code: str
    status: str
    operator: Operator
    remark: str = None
    provider_ref: str = None


@dataclass
class Composition:
    """One component of a manufactured item (KRA saveItemComposition)."""

    item_code: str
    component_item_code: str
    component_quantity: Decimal
    operator: Operator


# --------------------------------------------------------------- adapter

#: Every operation a backend may implement, in the order of the spec.
OPERATIONS = (
    "initialize", "sync_codes", "sync_item_classes", "fetch_branches",
    "fetch_notices", "register_item", "sign_sale", "sign_credit_note",
    "get_status", "fetch_purchases", "confirm_purchase", "report_stock_io",
    "save_stock_master", "fetch_imports", "update_import", "handle_callback",
    "save_item_composition", "sign_reverse_invoice",
)

REGISTRY = {}


def register(cls):
    """Class decorator: make a backend selectable on a device."""
    REGISTRY[cls.code] = cls
    return cls


def get_adapter_class(code):
    try:
        return REGISTRY[code]
    except KeyError:
        raise EtimsConfigError("There is no eTIMS backend called %r." % code) from None


class Adapter:
    """One backend. Subclasses implement whichever operations they support.

    An operation not overridden raises ``NotSupported``, and ``supports()``
    answers from the same fact, so the UI hides what would only fail.
    """

    code = None
    label = None
    #: Shown next to the label, and on the device, so nobody mistakes it for
    #: a certified route.
    experimental = False
    #: True where the other side refuses an invoice number out of order, so
    #: the queue must send one invoice at a time per device, in number order.
    strict_sequence = False
    #: Environments this backend may be used in.
    environments = ("test", "production")

    #: Seconds, for connecting and again for the answer. A number rather than
    #: a (connect, read) pair, as everywhere else in Odoo.
    timeout = 30.0

    def __init__(self, device, *, recorder=None, timeout=None, session=None):
        self.device = device
        self.recorder = recorder
        if timeout is not None:
            self.timeout = timeout
        self._session = session

    # ----------------------------------------------------------- discovery

    @classmethod
    def is_available(cls):
        """False when a library the backend needs is missing."""
        return True

    @classmethod
    def unavailable_reason(cls):
        return None

    @classmethod
    def supports(cls, operation):
        mine = getattr(cls, operation, None)
        return mine is not None and mine is not getattr(Adapter, operation, None)

    def _unsupported(self, operation):
        raise NotSupported(self.label, operation.replace("_", " ").capitalize())

    # ---------------------------------------------------------- operations

    def initialize(self):
        self._unsupported("initialize")

    def sync_codes(self, since=None):
        """A list of code classes: ``{"class": "04", "name": ..., "codes": [
        {"code", "name", "description", "sort_order", "active",
        "user_defined": (1, 2, 3)}]}``."""
        self._unsupported("sync_codes")

    def sync_item_classes(self, since=None):
        """``[{"code", "name", "level", "tax_type", "major_target", "active"}]``."""
        self._unsupported("sync_item_classes")

    def fetch_branches(self):
        """``[{"bhf_id", "name", "status", "is_head_office", "raw"}]``."""
        self._unsupported("fetch_branches")

    def fetch_notices(self, since=None):
        """``[{"number", "title", "contents", "url", "registered_at"}]``."""
        self._unsupported("fetch_notices")

    def register_item(self, item):
        """Returns the provider's item id, or None where it keeps none."""
        self._unsupported("register_item")

    def sign_sale(self, sale):
        self._unsupported("sign_sale")

    def sign_credit_note(self, sale):
        self._unsupported("sign_credit_note")

    def get_status(self, provider_ref, kra_invoice_no=None, kind="sale"):
        """Re-read a sale (or a reverse invoice) the provider accepted but had
        not signed yet."""
        self._unsupported("get_status")

    def fetch_purchases(self, since=None):
        """Supplier sales declared with our PIN: a list of dicts with
        supplier_pin, supplier_name, supplier_bhf_id, supplier_invoice_no,
        receipt_type, payment_type, confirmed_at, sale_date, total_amount,
        total_tax, provider_ref and ``lines`` (dicts in ``Line``'s names)."""
        self._unsupported("fetch_purchases")

    def confirm_purchase(self, purchase):
        self._unsupported("confirm_purchase")

    def report_stock_io(self, stock_io):
        self._unsupported("report_stock_io")

    def save_stock_master(self, stock_master):
        self._unsupported("save_stock_master")

    def fetch_imports(self, since=None):
        self._unsupported("fetch_imports")

    def update_import(self, update):
        self._unsupported("update_import")

    def handle_callback(self, payload):
        """Turn an unsigned notification into something to re-fetch:
        ``{"kind": "sale" | "item", "provider_ref": ...}``. Never trust the
        payload's own figures."""
        self._unsupported("handle_callback")

    def save_item_composition(self, composition):
        self._unsupported("save_item_composition")

    def sign_reverse_invoice(self, purchase):
        self._unsupported("sign_reverse_invoice")

    # ---------------------------------------------------------- transport

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def secrets(self):
        """Strings that must never be written anywhere."""
        return []

    def record(self, endpoint, request=None, response=None, *, status=None,
               error=None, duration_ms=None):
        if not self.recorder:
            return
        secrets = self.secrets()
        self.recorder(
            endpoint=endpoint,
            request=redact(request, secrets) if request is not None else None,
            response=redact(response, secrets) if response is not None else None,
            status=status,
            error=redact(str(error), secrets) if error else None,
            duration_ms=duration_ms,
        )

    def http(self, method, url, *, endpoint=None, json_body=None, headers=None,
             params=None, log_body=None):
        """One HTTP exchange, recorded, with every failure turned into an
        ``EtimsError`` that says whether the request may have arrived.

        Returns the decoded JSON body (or None for an empty one) together with
        the HTTP status code; the caller interprets the body.
        """
        endpoint = endpoint or url
        logged_request = log_body if log_body is not None else json_body
        started = time.monotonic()
        data = None
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False,
                              separators=(",", ":"), default=str).encode()
        all_headers = {"Accept": "application/json"}
        if data is not None:
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers or {})
        try:
            response = self.session.request(
                method, url, data=data, headers=all_headers, params=params,
                timeout=self.timeout)
        except requests.ConnectTimeout as exc:
            raise self._transport_error(
                endpoint, logged_request, started,
                "Could not connect to %s: timed out." % self.label,
                processed=False) from exc
        except requests.ReadTimeout as exc:
            raise self._transport_error(
                endpoint, logged_request, started,
                "%s did not answer in time." % self.label,
                processed=method != "GET") from exc
        except requests.ConnectionError as exc:
            processed = method != "GET" and not _never_connected(exc)
            raise self._transport_error(
                endpoint, logged_request, started,
                "The connection to %s failed." % self.label,
                processed=processed) from exc
        except requests.RequestException as exc:
            raise self._transport_error(
                endpoint, logged_request, started,
                "The request to %s failed: %s" % (self.label, exc),
                processed=method != "GET") from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = None
        self.record(endpoint, logged_request,
                    body if body is not None else (response.text or "")[:2000],
                    status=response.status_code, duration_ms=duration_ms)
        return body, response.status_code

    def _transport_error(self, endpoint, request, started, message, *, processed):
        self.record(endpoint, request, None, error=message,
                    duration_ms=int((time.monotonic() - started) * 1000))
        return EtimsError(message, retryable=True,
                          may_have_been_processed=processed)


def _never_connected(exc):
    """True when no connection was made, so nothing was sent."""
    from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
    if isinstance(exc, requests.exceptions.SSLError):
        return True
    reason = exc.args[0] if exc.args else None
    reason = getattr(reason, "reason", reason)
    return isinstance(reason, (NewConnectionError, ConnectTimeoutError))
