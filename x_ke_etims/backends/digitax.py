# -*- coding: utf-8 -*-
"""DigiTax's eTIMS REST API (https://api.digitax.tech/ke/v2).

DigiTax runs the control unit and talks to KRA itself; we talk to DigiTax with
an ``X-API-Key`` header. A business has separate TEST and LIVE keys, so a
device's environment is simply which key it holds.

Three things about DigiTax shape this adapter:

**Items exist at DigiTax first.** A sale references items by DigiTax's own item
id, so registering a product returns that id and the product keeps one per
device.

**A sale is not signed when it is accepted.** ``POST /sales`` answers PENDING,
FAILED or COMPLETED. A pending sale is stored as pending and finished later,
either by polling ``GET /sales/{id}`` from the queue or by DigiTax's callback.

**Callbacks are not signed.** Nothing in a notification proves it came from
DigiTax, so ``handle_callback`` reads only *which* record changed, and the
caller re-fetches that record with our own key before believing anything.

Before go-live
--------------
The endpoints are the ones DigiTax documents publicly. The JSON field names of
request and response bodies are kept in this file only -- ``_sale_body``,
``_item_body``, ``_receipt`` and friends -- and responses are read tolerantly
(several spellings, an optional ``data`` wrapper), so aligning with DigiTax's
reference is an edit here and to the fixtures in ``tests/digitax_fixtures.py``.
Production stays blocked until the device records DigiTax's KRA
certification.
"""

from .base import (
    Adapter, DeviceInfo, EtimsConfigError, EtimsError, Receipt, first,
    json_number, kra_date, parse_kra_date, parse_kra_datetime, register,
)

BASE_URL = "https://api.digitax.tech/ke/v2"

STATUS = {
    "COMPLETED": "signed",
    "SUCCESS": "signed",
    "SIGNED": "signed",
    "PENDING": "pending",
    "PROCESSING": "pending",
    "QUEUED": "pending",
    "FAILED": "failed",
    "REJECTED": "failed",
    "ERROR": "failed",
}


def _unwrap(body):
    """DigiTax answers either the record or ``{"data": record}``."""
    if isinstance(body, dict) and isinstance(body.get("data"), (dict, list)):
        return body["data"]
    return body


def _rows(body, *keys):
    body = _unwrap(body)
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        for key in keys + ("results", "items", "data"):
            if isinstance(body.get(key), list):
                return [row for row in body[key] if isinstance(row, dict)]
    return []


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


@register
class DigiTaxAdapter(Adapter):
    code = "digitax"
    label = "DigiTax"

    @property
    def base_url(self):
        return (self.device.base_url or BASE_URL).rstrip("/")

    def secrets(self):
        device = self.device.sudo()
        return [device.api_key, device.callback_token]

    def _headers(self):
        key = self.device.sudo().api_key
        if not key:
            raise EtimsConfigError(
                "Enter the DigiTax API key for this device (the TEST key for "
                "Test, the LIVE key for Production).")
        return {"X-API-Key": key}

    def request(self, method, path, *, body=None, params=None):
        payload, status = self.http(
            method, self.base_url + path, endpoint="%s %s" % (method, path),
            json_body=body, headers=self._headers(), params=params)
        if 200 <= status < 300:
            return payload
        message = first(payload, "message", "error", "detail") if isinstance(payload, dict) \
            else None
        message = message or "DigiTax answered HTTP %s." % status
        if status in (401, 403):
            raise EtimsConfigError("DigiTax refused the API key: %s" % message, code=str(status))
        if status == 409:
            raise EtimsError(message, code="409", duplicate=True)
        raise EtimsError(
            message, code=str(status),
            retryable=status == 429 or status >= 500,
            may_have_been_processed=status >= 500 and method != "GET")

    # ------------------------------------------------------- reference data

    def initialize(self):
        info = _unwrap(self.request("GET", "/etims-info")) or {}
        return DeviceInfo(
            tin=first(info, "pin", "kra_pin", "tin"),
            bhf_id=first(info, "branch_id", "bhf_id", "bhfId"),
            taxpayer_name=first(info, "business_name", "taxpayer_name", "name"),
            branch_name=first(info, "branch_name", "bhfNm"),
            scu_id=first(info, "scu_id", "sdc_id", "sdcId", "cu_serial_number"),
            mrc_no=first(info, "mrc_no", "mrcNo"),
            device_id=first(info, "device_serial", "device_id", "dvcSrlNo"),
            raw=info if isinstance(info, dict) else {},
        )

    def fetch_branches(self):
        return [
            {
                "bhf_id": first(row, "branch_id", "bhf_id", "bhfId"),
                "name": first(row, "name", "branch_name", "bhfNm"),
                "status": first(row, "status", "bhfSttsCd"),
                "is_head_office": bool(first(row, "is_head_office", "hq", default=False))
                or first(row, "hqYn") == "Y",
                "raw": row,
            }
            for row in _rows(self.request("GET", "/branches"), "branches")
        ]

    def fetch_notices(self, since=None):
        return [
            {
                "number": _int(first(row, "notice_number", "number", "noticeNo", "id")),
                "title": first(row, "title"),
                "contents": first(row, "content", "contents", "cont"),
                "url": first(row, "url", "detail_url", "dtlUrl"),
                "registered_at": parse_kra_datetime(first(row, "created_at", "regDt")),
            }
            for row in _rows(self.request("GET", "/notices"), "notices")
        ]

    # ---------------------------------------------------------------- items

    def _item_body(self, item):
        return {
            "name": item.name[:200],
            "item_code": item.item_code,
            "item_class_code": item.item_class_code,
            "product_type": item.product_type,
            "origin_country": item.origin_country,
            "packaging_unit": item.packaging_unit,
            "quantity_unit": item.quantity_unit,
            "tax_type": item.tax_type,
            "default_unit_price": json_number(item.default_price),
            "barcode": item.barcode or None,
            "active": item.active,
            "callback_url": item.callback_url,
        }

    def register_item(self, item):
        if item.provider_item_id:
            self.request("PUT", "/items/%s" % item.provider_item_id,
                         body=self._item_body(item))
            return item.provider_item_id
        created = _unwrap(self.request("POST", "/items", body=self._item_body(item))) or {}
        item_id = first(created, "id", "item_id")
        if not item_id:
            raise EtimsError("DigiTax created the item but returned no id.",
                             may_have_been_processed=True)
        return str(item_id)

    # ---------------------------------------------------------------- sales

    def _line(self, line):
        if not line.provider_item_id:
            raise EtimsConfigError(
                "%s is not registered with DigiTax on this device." % line.name)
        return {
            "item_id": line.provider_item_id,
            "quantity": json_number(line.quantity),
            "unit_price": json_number(line.unit_price),
            "discount_rate": json_number(line.discount_rate),
            "discount_amount": json_number(line.discount_amount),
            "tax_type": line.tax_type,
            "taxable_amount": json_number(line.taxable_amount),
            "tax_amount": json_number(line.tax_amount),
            "total_amount": json_number(line.total_amount),
        }

    def _sale_body(self, sale):
        totals = sale.totals()
        return {
            "invoice_number": sale.trader_invoice_no[:50],
            "trader_invoice_number": sale.trader_invoice_no[:50],
            "kra_invoice_number": sale.invoice_no,
            "invoice_date": kra_date(sale.sale_date),
            "customer_pin": sale.customer_pin or None,
            "customer_name": (sale.customer_name or None) and sale.customer_name[:60],
            "customer_phone": sale.customer_phone or None,
            "payment_type": sale.payment_type,
            "total_taxable_amount": json_number(totals.total_taxable),
            "total_tax_amount": json_number(totals.total_tax),
            "total_amount": json_number(totals.total_amount),
            "callback_url": sale.callback_url,
            "items": [self._line(line) for line in sale.lines],
        }

    def _receipt(self, data, sale=None):
        data = _unwrap(data) or {}
        status = STATUS.get(str(first(data, "status", "state", default="")).upper())
        if status is None:
            # No status but a signature: DigiTax answered with the receipt.
            status = "signed" if first(data, "receipt_signature", "rcptSign",
                                       "signature") else "pending"
        receipt_block = data.get("receipt") if isinstance(data.get("receipt"), dict) else {}
        source = {**data, **receipt_block}
        device = self.device
        return Receipt(
            status=status,
            kra_invoice_no=_int(first(source, "kra_invoice_number", "invoice_no",
                                      "invcNo")) or (sale.invoice_no if sale else None),
            receipt_no=_int(first(source, "receipt_number", "curRcptNo",
                                  "current_receipt_number")),
            total_receipt_no=_int(first(source, "total_receipt_number", "totRcptNo")),
            internal_data=first(source, "internal_data", "intrlData"),
            signature=first(source, "receipt_signature", "rcptSign", "signature"),
            scu_id=first(source, "scu_id", "sdc_id", "sdcId") or device.scu_id,
            mrc_no=first(source, "mrc_no", "mrcNo") or device.mrc_no,
            signed_at=parse_kra_datetime(first(source, "signed_at", "sdcDateTime",
                                               "sdc_date_time", "completed_at")),
            # DigiTax hands back the KRA verification link itself.
            qr_url=first(source, "etims_url", "qr_url", "verification_url"),
            provider_ref=str(first(data, "id", "sale_id", default="")) or None,
            error=first(source, "error", "error_message", "message")
            if status == "failed" else None,
            raw=data if isinstance(data, dict) else {},
        )

    def sign_sale(self, sale):
        answer = self.request("POST", "/sales", body=self._sale_body(sale))
        return self._receipt(answer, sale)

    def sign_credit_note(self, sale):
        if not sale.original_provider_ref:
            raise EtimsConfigError(
                "The invoice being credited has no DigiTax sale id; it must be "
                "signed through DigiTax first.")
        body = self._sale_body(sale)
        body.update({
            "original_sale_id": sale.original_provider_ref,
            "original_kra_invoice_number": sale.original_invoice_no,
            "reason_code": sale.reason_code,
            "credit_note_date": kra_date(sale.refunded_at or sale.confirmed_at),
        })
        answer = self.request("POST", "/credit-notes", body=body)
        return self._receipt(answer, sale)

    def get_status(self, provider_ref, kra_invoice_no=None):
        answer = self.request("GET", "/sales/%s" % provider_ref)
        receipt = self._receipt(answer)
        receipt.provider_ref = receipt.provider_ref or provider_ref
        receipt.kra_invoice_no = receipt.kra_invoice_no or kra_invoice_no
        return receipt

    def fetch_item(self, provider_item_id):
        return _unwrap(self.request("GET", "/items/%s" % provider_item_id)) or {}

    def handle_callback(self, payload):
        if not isinstance(payload, dict):
            raise EtimsError("The DigiTax notification is not a JSON object.")
        event = str(first(payload, "event", "type", "event_type", default="")).lower()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if event.startswith("item"):
            kind = "item"
            ref = first(data, "item_id", "id")
        elif event.startswith("sale") or event.startswith("credit"):
            kind = "sale"
            ref = first(data, "sale_id", "id")
        else:
            raise EtimsError("Unknown DigiTax notification %r." % event)
        if not ref:
            raise EtimsError("The DigiTax notification names no record.")
        return {"kind": kind, "provider_ref": str(ref)}

    # ------------------------------------------------------------ purchases

    def fetch_purchases(self, since=None):
        params = {"since": kra_date(since)} if since else None
        purchases = []
        for row in _rows(self.request("GET", "/purchases", params=params), "purchases"):
            purchases.append({
                "supplier_pin": first(row, "supplier_pin", "spplrTin"),
                "supplier_name": first(row, "supplier_name", "spplrNm"),
                "supplier_bhf_id": first(row, "supplier_branch_id", "spplrBhfId"),
                "supplier_invoice_no": _int(first(row, "supplier_invoice_number",
                                                  "spplrInvcNo")),
                "receipt_type": first(row, "receipt_type", "rcptTyCd"),
                "payment_type": first(row, "payment_type", "pmtTyCd"),
                "confirmed_at": parse_kra_datetime(first(row, "confirmed_at", "cfmDt")),
                "sale_date": parse_kra_date(first(row, "invoice_date", "salesDt")),
                "total_taxable": first(row, "total_taxable_amount", "totTaxblAmt"),
                "total_tax": first(row, "total_tax_amount", "totTaxAmt"),
                "total_amount": first(row, "total_amount", "totAmt"),
                "remark": first(row, "remark"),
                "provider_ref": str(first(row, "id", default="")) or None,
                "lines": [
                    {
                        "seq": _int(first(line, "sequence", "itemSeq")),
                        "item_class_code": first(line, "item_class_code", "itemClsCd"),
                        "item_code": first(line, "item_code", "itemCd"),
                        "name": first(line, "name", "item_name", "itemNm"),
                        "packaging_unit": first(line, "packaging_unit", "pkgUnitCd"),
                        "quantity_unit": first(line, "quantity_unit", "qtyUnitCd"),
                        "quantity": first(line, "quantity", "qty"),
                        "unit_price": first(line, "unit_price", "prc"),
                        "supply_amount": first(line, "supply_amount", "splyAmt"),
                        "discount_amount": first(line, "discount_amount", "dcAmt"),
                        "tax_type": first(line, "tax_type", "taxTyCd"),
                        "taxable_amount": first(line, "taxable_amount", "taxblAmt"),
                        "tax_amount": first(line, "tax_amount", "taxAmt"),
                        "total_amount": first(line, "total_amount", "totAmt"),
                        "provider_ref": str(first(line, "id", default="")) or None,
                    }
                    for line in _rows(row.get("items") or row.get("itemList") or [])
                ],
                "raw": row,
            })
        return purchases

    def confirm_purchase(self, purchase):
        if not purchase.provider_ref:
            raise EtimsConfigError("This purchase did not come from DigiTax.")
        self.request("POST", "/purchases/%s/link-items" % purchase.provider_ref, body={
            "items": [
                {
                    "sequence": line.seq,
                    "item_id": line.provider_item_id,
                    "quantity": json_number(line.quantity),
                }
                for line in purchase.lines
            ],
        })

    def sign_reverse_invoice(self, purchase):
        """A reverse invoice: we issue the eTIMS invoice for a purchase from a
        seller who cannot (a small farmer), and DigiTax signs it."""
        totals = purchase.totals()
        answer = self.request("POST", "/reverse-invoices", body={
            "supplier_name": (purchase.supplier_name or "")[:60],
            "supplier_pin": purchase.supplier_pin or None,
            "invoice_number": str(purchase.invoice_no),
            "invoice_date": kra_date(purchase.purchase_date),
            "payment_type": purchase.payment_type,
            "total_amount": json_number(totals.total_amount),
            "total_tax_amount": json_number(totals.total_tax),
            "items": [
                {
                    "item_id": line.provider_item_id,
                    "name": line.name[:200],
                    "quantity": json_number(line.quantity),
                    "unit_price": json_number(line.unit_price),
                    "tax_type": line.tax_type,
                    "taxable_amount": json_number(line.taxable_amount),
                    "tax_amount": json_number(line.tax_amount),
                    "total_amount": json_number(line.total_amount),
                }
                for line in purchase.lines
            ],
        })
        return self._receipt(answer)

    # ---------------------------------------------------------------- stock

    #: KRA movement types DigiTax takes as transfers or adjustments. Sales,
    #: purchases and imports move DigiTax's stock as part of those records.
    TRANSFER_TYPES = {"04", "13"}
    ADJUSTMENT_TYPES = {"05", "06", "14", "15", "16"}

    def report_stock_io(self, stock_io):
        lines = [
            {
                "item_id": line.provider_item_id,
                "quantity": json_number(line.quantity),
                "unit_price": json_number(line.unit_price),
            }
            for line in stock_io.lines
        ]
        if stock_io.io_type in self.TRANSFER_TYPES:
            self.request("POST", "/stock-transfer", body={
                "reference": str(stock_io.sar_no),
                "direction": "in" if stock_io.io_type < "10" else "out",
                "branch_id": stock_io.customer_bhf_id,
                "date": kra_date(stock_io.occurred_on),
                "items": lines,
            })
        elif stock_io.io_type in self.ADJUSTMENT_TYPES:
            self.request("PUT", "/stock-adjust", body={
                "reference": str(stock_io.sar_no),
                "movement_type": stock_io.io_type,
                "date": kra_date(stock_io.occurred_on),
                "remark": stock_io.remark,
                "items": lines,
            })
        else:
            raise EtimsError(
                "DigiTax records movement type %s with its sale, purchase or "
                "import, not separately." % stock_io.io_type, code="handled_by_provider")

    # -------------------------------------------------------------- imports

    def fetch_imports(self, since=None):
        return [
            {
                "task_code": first(row, "task_code", "taskCd"),
                "declaration_date": parse_kra_date(first(row, "declaration_date", "dclDe")),
                "item_seq": _int(first(row, "item_sequence", "itemSeq")),
                "declaration_no": first(row, "declaration_number", "dclNo"),
                "hs_code": first(row, "hs_code", "hsCd"),
                "name": first(row, "item_name", "name", "itemNm"),
                "status": first(row, "status", "imptItemSttsCd"),
                "origin_country": first(row, "origin_country", "orgnNatCd"),
                "export_country": first(row, "export_country", "exptNatCd"),
                "package_quantity": first(row, "package_quantity", "pkg"),
                "packaging_unit": first(row, "packaging_unit", "pkgUnitCd"),
                "quantity": first(row, "quantity", "qty"),
                "quantity_unit": first(row, "quantity_unit", "qtyUnitCd"),
                "gross_weight": first(row, "gross_weight", "totWt"),
                "net_weight": first(row, "net_weight", "netWt"),
                "supplier_name": first(row, "supplier_name", "spplrNm"),
                "agent_name": first(row, "agent_name", "agntNm"),
                "invoice_amount": first(row, "invoice_amount", "invcFcurAmt"),
                "invoice_currency": first(row, "invoice_currency", "invcFcurCd"),
                "exchange_rate": first(row, "exchange_rate", "invcFcurExcrt"),
                "provider_ref": str(first(row, "id", default="")) or None,
                "raw": row,
            }
            for row in _rows(self.request("GET", "/imports"), "imports")
        ]

    def update_import(self, update):
        if not update.provider_ref:
            raise EtimsConfigError("This import did not come from DigiTax.")
        self.request("PUT", "/imports/%s/link" % update.provider_ref, body={
            "item_code": update.item_code,
            "status": update.status,
            "remark": update.remark,
        })
