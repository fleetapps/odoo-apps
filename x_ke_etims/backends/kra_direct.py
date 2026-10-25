# -*- coding: utf-8 -*-
"""Our own client for KRA's OSCU API.

Every endpoint and field name here is from KRA's OSCU Specification v2.0 as
the MIT-licensed ``etims`` library records it, section by section, with the
places where the specification's tables and its samples disagree resolved the
way the live API answers. That library is an independent second reading of the
same document; ``kra_etims_lib`` uses it directly, and the two backends are
tested against the same fake KRA so that they cannot quietly drift apart.

``saveItemComposition`` is the one endpoint neither the library nor its fake
KRA covers. Its field names follow the specification's table and are the first
thing to check against the sandbox.

Production needs KRA's certification of the integration -- Odin's third-party
vendor certificate or the client's own self-integration certificate -- and the
device refuses Production until ``certification_ref`` says which.

Transport
---------
JSON over HTTPS POST to ``{base}/{endpoint}``. ``tin``, ``bhfId`` and
``cmcKey`` go in the headers *and* the body: the specification lists them as
request fields but leaves ``cmcKey`` out of its samples, while result code 900
("There is no Header information") points at headers. Sending both satisfies
either reading. ``resultCd`` "000" is success and "001" an empty search.
"""

from urllib.parse import quote

from .base import (
    TAX_TYPE_LETTERS, Adapter, DeviceInfo, EtimsConfigError, EtimsError,
    Receipt, first, json_number, kra_date, kra_datetime, parse_kra_date,
    parse_kra_datetime, register,
)

API_URLS = {
    "test": "https://etims-api-sbx.kra.go.ke/etims-api",
    "production": "https://etims-api.kra.go.ke/etims-api",
}

#: The public page a receipt's QR code opens. Not in the OSCU specification;
#: it is the address KRA's own receipts use, and the TIS specification's
#: sample receipt shows it with "{KRA-PIN+BHF-ID+RcpSignture}" as the query.
RECEIPT_URLS = {
    "test": "https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData",
    "production": "https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData",
}

SUCCESS = "000"
NO_RESULTS = "001"

#: Result codes from section 4.18 of the specification, by what the queue
#: should do about them.
DUPLICATE_CODES = {"921", "994"}
SERVER_CODES = {"990", "991", "992", "993", "995", "999"}
RESULT_MESSAGES = {
    "900": "There is no Header information",
    "901": "It is not valid device",
    "902": "This device is installed",
    "903": "Only OSCU device can be verified",
    "905": "Only OSCU device can be verified",
    "910": "Request parameter error",
    "911": "There is no request full text",
    "912": "There is a request Method error",
    "921": "Sales or sales invoice data which is declared cannot be received",
    "922": "Sales invoice data can be received after receiving the sales data",
    "990": "The maximum number of views are exceeded",
    "991": "There is an error during registration",
    "992": "There is an error during modification",
    "993": "There is an error during deletion",
    "994": "There is an overlapped Data",
    "995": "There is no downloaded file",
    "999": "There is an unknown error",
}


def receipt_qr_url(environment, tin, bhf_id, signature):
    """The link a KRA receipt's QR code encodes: PIN, branch and signature."""
    if not signature:
        return None
    base = RECEIPT_URLS.get(environment, RECEIPT_URLS["test"])
    return "%s?Data=%s" % (base, quote("%s%s%s" % (tin, bhf_id, signature), safe=""))


def _amounts(totals):
    body = {}
    for letter in TAX_TYPE_LETTERS:
        body["taxblAmt" + letter] = json_number(totals.taxable[letter])
        body["taxRt" + letter] = json_number(totals.rates[letter])
        body["taxAmt" + letter] = json_number(totals.tax[letter])
    body["totTaxblAmt"] = json_number(totals.total_taxable)
    body["totTaxAmt"] = json_number(totals.total_tax)
    body["totAmt"] = json_number(totals.total_amount)
    return body


def _operator(operator, registrant=True):
    body = {}
    if registrant:
        body.update(regrId=operator.id[:20], regrNm=operator.name[:60])
    body.update(modrId=operator.id[:20], modrNm=operator.name[:60])
    return body


def _cut(text, length):
    return text[:length] if text else None


@register
class KraDirectAdapter(Adapter):
    code = "kra_direct"
    label = "KRA OSCU (direct)"
    strict_sequence = True

    # ------------------------------------------------------------ transport

    @property
    def base_url(self):
        return (self.device.base_url or API_URLS[self.device.environment]).rstrip("/")

    def secrets(self):
        return [self.device.sudo().cmc_key]

    def _auth(self):
        device = self.device.sudo()
        if not device.cmc_key:
            raise EtimsConfigError(
                "The device has no communication key yet. Initialise it first.")
        return {"tin": device.tin, "bhfId": device.bhf_id, "cmcKey": device.cmc_key}

    def call(self, path, body, *, auth=True):
        """POST one request; return the envelope for 000 and 001, raise otherwise."""
        headers = self._auth() if auth else {}
        payload = {**headers, **body}
        envelope, status = self.http(
            "POST", self.base_url + path, endpoint=path, json_body=payload,
            headers=headers)
        if status != 200:
            raise EtimsError(
                "KRA answered %s with HTTP %s." % (path, status),
                retryable=status == 429 or status >= 500,
                may_have_been_processed=status >= 500)
        if not isinstance(envelope, dict) or "resultCd" not in envelope:
            raise EtimsError("KRA's answer to %s has no result code." % path,
                             retryable=True, may_have_been_processed=True)
        code = str(envelope["resultCd"]).strip()
        if code in (SUCCESS, NO_RESULTS):
            envelope["resultCd"] = code
            return envelope
        message = str(envelope.get("resultMsg") or RESULT_MESSAGES.get(code)
                      or "Unknown result code").strip()
        raise EtimsError(
            message, code=code,
            duplicate=code in DUPLICATE_CODES,
            retryable=code in SERVER_CODES,
        )

    def _search(self, path, list_key, since):
        envelope = self.call(path, {"lastReqDt": kra_datetime(since) or "20000101000000"})
        data = envelope.get("data")
        if envelope["resultCd"] == NO_RESULTS or not isinstance(data, dict):
            return []
        rows = data.get(list_key)
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    # ------------------------------------------------------- reference data

    def initialize(self):
        device = self.device
        if not device.serial:
            raise EtimsConfigError("Enter the device serial number KRA approved first.")
        envelope = self.call("/selectInitOsdcInfo", {
            "tin": device.tin, "bhfId": device.bhf_id, "dvcSrlNo": device.serial,
        }, auth=False)
        info = (envelope.get("data") or {}).get("info")
        if not isinstance(info, dict):
            raise EtimsError("Device initialisation returned no device information.")
        if not info.get("cmcKey"):
            raise EtimsError("Device initialisation returned no communication key.")
        return DeviceInfo(
            tin=first(info, "tin"),
            bhf_id=first(info, "bhfId"),
            taxpayer_name=first(info, "taxprNm"),
            branch_name=first(info, "bhfNm"),
            # The specification's table says "sdicId"; its sample and the API "sdcId".
            scu_id=first(info, "sdcId", "sdicId"),
            mrc_no=first(info, "mrcNo"),
            device_id=first(info, "dvcId"),
            cmc_key=info.get("cmcKey"),
            raw={k: v for k, v in info.items() if k != "cmcKey"},
        )

    def sync_codes(self, since=None):
        classes = []
        for row in self._search("/selectCodeList", "clsList", since):
            classes.append({
                "class": first(row, "cdCls"),
                "name": first(row, "cdClsNm"),
                "codes": [
                    {
                        "code": first(code, "cd"),
                        "name": first(code, "cdNm"),
                        "description": first(code, "cdDesc"),
                        "sort_order": first(code, "srtOrd"),
                        "active": first(code, "useYn", default="Y") == "Y",
                        "user_defined": (first(code, "userDfnCd1"),
                                         first(code, "userDfnCd2"),
                                         first(code, "userDfnCd3")),
                    }
                    for code in row.get("dtlList") or [] if isinstance(code, dict)
                ],
            })
        return classes

    def sync_item_classes(self, since=None):
        return [
            {
                "code": first(row, "itemClsCd"),
                "name": first(row, "itemClsNm"),
                "level": first(row, "itemClsLvl"),
                "tax_type": first(row, "taxTyCd"),
                "major_target": first(row, "mjrTgYn") == "Y",
                "active": first(row, "useYn", default="Y") == "Y",
            }
            for row in self._search("/selectItemClsList", "itemClsList", since)
        ]

    def fetch_branches(self):
        return [
            {
                "bhf_id": first(row, "bhfId"),
                "name": first(row, "bhfNm"),
                "status": first(row, "bhfSttsCd"),
                "is_head_office": first(row, "hqYn") == "Y",
                "raw": row,
            }
            for row in self._search("/selectBhfList", "bhfList", None)
        ]

    def fetch_notices(self, since=None):
        return [
            {
                "number": first(row, "noticeNo"),
                "title": first(row, "title"),
                "contents": first(row, "cont"),
                "url": first(row, "dtlUrl"),
                "registered_at": parse_kra_datetime(first(row, "regDt")),
            }
            for row in self._search("/selectNoticeList", "noticeList", since)
        ]

    # ---------------------------------------------------------------- items

    def register_item(self, item):
        body = {
            "itemCd": item.item_code,
            "itemClsCd": item.item_class_code,
            "itemTyCd": item.product_type,
            "itemNm": _cut(item.name, 200),
            "itemStdNm": None,
            "orgnNatCd": item.origin_country,
            "pkgUnitCd": item.packaging_unit,
            "qtyUnitCd": item.quantity_unit,
            "taxTyCd": item.tax_type,
            "btchNo": None,
            "bcd": _cut(item.barcode, 20),
            "dftPrc": json_number(item.default_price),
            "grpPrcL1": None, "grpPrcL2": None, "grpPrcL3": None,
            "grpPrcL4": None, "grpPrcL5": None,
            "addInfo": None,
            "sftyQty": None,
            "isrcAplcbYn": "N",
            "useYn": "Y" if item.active else "N",
            **_operator(item.operator),
        }
        self.call("/saveItem", body)
        return None

    def save_item_composition(self, composition):
        # Field names from the specification's table; not covered by the
        # etims library or its fake KRA. Verify against the sandbox.
        self.call("/saveItemComposition", {
            "itemCd": composition.item_code,
            "cpstItemCd": composition.component_item_code,
            "cpstQty": json_number(composition.component_quantity),
            "regrId": composition.operator.id[:20],
            "regrNm": composition.operator.name[:60],
        })

    # ---------------------------------------------------------------- sales

    def _sale_body(self, sale):
        credit = sale.is_credit_note
        totals = sale.totals()
        body = {
            "trdInvcNo": _cut(sale.trader_invoice_no, 50),
            "invcNo": sale.invoice_no,
            "orgInvcNo": sale.original_invoice_no or 0,
            "custTin": sale.customer_pin or None,
            "custNm": _cut(sale.customer_name, 60),
            "salesTyCd": sale.sales_type,
            "rcptTyCd": sale.receipt_type,
            "pmtTyCd": sale.payment_type,
            "salesSttsCd": "02",
            "cfmDt": kra_datetime(sale.confirmed_at),
            "salesDt": kra_date(sale.sale_date),
            "stockRlsDt": None,
            "cnclReqDt": None,
            "cnclDt": None,
            "rfdDt": kra_datetime(sale.refunded_at or sale.confirmed_at) if credit else None,
            "rfdRsnCd": sale.reason_code if credit else None,
            "totItemCnt": len(sale.lines),
            **_amounts(totals),
            "prchrAcptcYn": "N",
            "remark": _cut(sale.remark, 400),
            **_operator(sale.operator),
            "receipt": {
                "custTin": sale.customer_pin or None,
                "custMblNo": _cut(sale.customer_phone, 20),
                "rcptPbctDt": kra_datetime(sale.confirmed_at),
                "trdeNm": _cut(sale.trade_name, 20),
                "adrs": None,
                "topMsg": None,
                "btmMsg": None,
                "prchrAcptcYn": "N",
            },
            "itemList": [
                {
                    "itemSeq": line.seq,
                    "itemClsCd": line.item_class_code,
                    "itemCd": line.item_code,
                    "itemNm": _cut(line.name, 200),
                    "bcd": _cut(line.barcode, 20),
                    "pkgUnitCd": line.packaging_unit,
                    "pkg": json_number(line.package_quantity or line.quantity),
                    "qtyUnitCd": line.quantity_unit,
                    "qty": json_number(line.quantity),
                    "prc": json_number(line.unit_price),
                    "splyAmt": json_number(line.supply_amount),
                    "dcRt": json_number(line.discount_rate),
                    "dcAmt": json_number(line.discount_amount),
                    "isrccCd": None, "isrccNm": None, "isrcRt": None, "isrcAmt": None,
                    "taxTyCd": line.tax_type,
                    "taxblAmt": json_number(line.taxable_amount),
                    # The table names it totTaxAmt; the sample and every other
                    # line object in the API say taxAmt.
                    "taxAmt": json_number(line.tax_amount),
                    "totAmt": json_number(line.total_amount),
                }
                for line in sale.lines
            ],
        }
        return body

    def sign_sale(self, sale):
        return self._sign(sale)

    def sign_credit_note(self, sale):
        if not sale.original_invoice_no:
            raise EtimsConfigError("A credit note needs the KRA invoice number it credits.")
        if not sale.reason_code:
            raise EtimsConfigError("A credit note needs a KRA credit note reason.")
        return self._sign(sale)

    def _sign(self, sale):
        envelope = self.call("/saveTrnsSalesOsdc", self._sale_body(sale))
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise EtimsError(
                "KRA accepted invoice %s but returned no receipt data." % sale.invoice_no,
                may_have_been_processed=True, retryable=True)
        # Keys are stripped: the specification's samples carry "curRcptNo ".
        data = {str(k).strip(): v for k, v in data.items()}
        device = self.device
        signature = first(data, "rcptSign")
        return Receipt(
            status="signed",
            kra_invoice_no=sale.invoice_no,
            receipt_no=_int(first(data, "curRcptNo")),
            total_receipt_no=_int(first(data, "totRcptNo")),
            internal_data=first(data, "intrlData"),
            signature=signature,
            scu_id=device.scu_id,
            mrc_no=device.mrc_no,
            signed_at=parse_kra_datetime(first(data, "sdcDateTime")),
            qr_url=receipt_qr_url(device.environment, device.tin, device.bhf_id, signature),
            raw=data,
        )

    # ------------------------------------------------------------ purchases

    def fetch_purchases(self, since=None):
        purchases = []
        for row in self._search("/selectTrnsPurchaseSalesList", "saleList", since):
            purchases.append({
                "supplier_pin": first(row, "spplrTin"),
                "supplier_name": first(row, "spplrNm"),
                "supplier_bhf_id": first(row, "spplrBhfId"),
                "supplier_invoice_no": _int(first(row, "spplrInvcNo")),
                "receipt_type": first(row, "rcptTyCd"),
                "payment_type": first(row, "pmtTyCd"),
                "confirmed_at": parse_kra_datetime(first(row, "cfmDt")),
                "sale_date": parse_kra_date(first(row, "salesDt")),
                "total_taxable": first(row, "totTaxblAmt"),
                "total_tax": first(row, "totTaxAmt"),
                "total_amount": first(row, "totAmt"),
                "remark": first(row, "remark"),
                "provider_ref": "%s/%s/%s" % (first(row, "spplrTin"),
                                              first(row, "spplrBhfId"),
                                              first(row, "spplrInvcNo")),
                # The CU invoice number on the supplier's own invoice, where
                # KRA sends the supplier's SCU ID with it.
                "supplier_cu_invoice": "%s/%s" % (first(row, "spplrSdcId"),
                                                  first(row, "spplrInvcNo"))
                if first(row, "spplrSdcId") else None,
                "lines": [_purchase_line(line) for line in row.get("itemList") or []
                          if isinstance(line, dict)],
                "raw": row,
            })
        return purchases

    def confirm_purchase(self, purchase):
        totals = purchase.totals()
        body = {
            "spplrTin": purchase.supplier_pin,
            "invcNo": purchase.invoice_no,
            "orgInvcNo": 0,
            "spplrBhfId": purchase.supplier_bhf_id,
            "spplrNm": _cut(purchase.supplier_name, 60),
            "spplrInvcNo": purchase.supplier_invoice_no,
            "regTyCd": purchase.registration_type,
            "pchsTyCd": "N",
            "rcptTyCd": purchase.receipt_type,
            "pmtTyCd": purchase.payment_type,
            "pchsSttsCd": purchase.status,
            "cfmDt": kra_datetime(purchase.confirmed_at),
            "pchsDt": kra_date(purchase.purchase_date),
            "wrhsDt": None,
            "cnclReqDt": None,
            "cnclDt": None,
            "rfdDt": None,
            "totItemCnt": len(purchase.lines),
            **_amounts(totals),
            "remark": _cut(purchase.remark, 400),
            **_operator(purchase.operator),
            "itemList": [
                {
                    "itemSeq": line.seq,
                    "itemCd": line.item_code,
                    "itemClsCd": line.item_class_code,
                    "itemNm": _cut(line.name, 200),
                    "bcd": _cut(line.barcode, 20),
                    "spplrItemClsCd": line.supplier_item_class_code,
                    "spplrItemCd": line.supplier_item_code,
                    "spplrItemNm": _cut(line.supplier_item_name, 200),
                    "pkgUnitCd": line.packaging_unit,
                    "pkg": json_number(line.package_quantity or line.quantity),
                    "qtyUnitCd": line.quantity_unit,
                    "qty": json_number(line.quantity),
                    "prc": json_number(line.unit_price),
                    "splyAmt": json_number(line.supply_amount),
                    "dcRt": json_number(line.discount_rate),
                    "dcAmt": json_number(line.discount_amount),
                    "taxblAmt": json_number(line.taxable_amount),
                    "taxTyCd": line.tax_type,
                    "taxAmt": json_number(line.tax_amount),
                    "totAmt": json_number(line.total_amount),
                    "itemExprDt": kra_date(line.expiry_date),
                }
                for line in purchase.lines
            ],
        }
        self.call("/insertTrnsPurchase", body)

    # ---------------------------------------------------------------- stock

    def report_stock_io(self, stock_io):
        lines = stock_io.lines
        body = {
            "sarNo": stock_io.sar_no,
            "orgSarNo": stock_io.original_sar_no or 0,
            "regTyCd": stock_io.registration_type,
            "custTin": stock_io.customer_pin,
            "custNm": _cut(stock_io.customer_name, 100),
            "custBhfId": stock_io.customer_bhf_id,
            "sarTyCd": stock_io.io_type,
            "ocrnDt": kra_date(stock_io.occurred_on),
            "totItemCnt": len(lines),
            "totTaxblAmt": json_number(sum((l.taxable_amount for l in lines), 0)),
            "totTaxAmt": json_number(sum((l.tax_amount for l in lines), 0)),
            "totAmt": json_number(sum((l.total_amount for l in lines), 0)),
            "remark": _cut(stock_io.remark, 400),
            **_operator(stock_io.operator),
            "itemList": [
                {
                    "itemSeq": line.seq,
                    "itemCd": line.item_code,
                    "itemClsCd": line.item_class_code,
                    "itemNm": _cut(line.name, 200),
                    "bcd": _cut(line.barcode, 20),
                    "pkgUnitCd": line.packaging_unit,
                    "pkg": json_number(line.package_quantity or line.quantity),
                    "qtyUnitCd": line.quantity_unit,
                    "qty": json_number(line.quantity),
                    "itemExprDt": kra_date(line.expiry_date),
                    "prc": json_number(line.unit_price),
                    "splyAmt": json_number(line.supply_amount),
                    "totDcAmt": json_number(line.discount_amount),
                    "taxblAmt": json_number(line.taxable_amount),
                    "taxTyCd": line.tax_type,
                    "taxAmt": json_number(line.tax_amount),
                    "totAmt": json_number(line.total_amount),
                }
                for line in lines
            ],
        }
        self.call("/insertStockIO", body)

    def save_stock_master(self, stock_master):
        self.call("/saveStockMaster", {
            "itemCd": stock_master.item_code,
            "rsdQty": json_number(stock_master.remaining_quantity),
            **_operator(stock_master.operator),
        })

    # -------------------------------------------------------------- imports

    def fetch_imports(self, since=None):
        return [
            {
                "task_code": first(row, "taskCd"),
                "declaration_date": parse_kra_date(first(row, "dclDe")),
                "item_seq": _int(first(row, "itemSeq")),
                "declaration_no": first(row, "dclNo"),
                "hs_code": first(row, "hsCd"),
                "name": first(row, "itemNm"),
                # The specification spells it both ways.
                "status": first(row, "imptItemSttsCd", "imptItemsttsCd"),
                "origin_country": first(row, "orgnNatCd"),
                "export_country": first(row, "exptNatCd"),
                "package_quantity": first(row, "pkg"),
                "packaging_unit": first(row, "pkgUnitCd"),
                "quantity": first(row, "qty"),
                "quantity_unit": first(row, "qtyUnitCd"),
                "gross_weight": first(row, "totWt"),
                "net_weight": first(row, "netWt"),
                "supplier_name": first(row, "spplrNm"),
                "agent_name": first(row, "agntNm"),
                "invoice_amount": first(row, "invcFcurAmt"),
                "invoice_currency": first(row, "invcFcurCd"),
                "exchange_rate": first(row, "invcFcurExcrt"),
                "provider_ref": "%s/%s" % (first(row, "taskCd"), first(row, "itemSeq")),
                "raw": row,
            }
            for row in self._search("/selectImportItemList", "itemList", since)
        ]

    def update_import(self, update):
        self.call("/updateImportItem", {
            "taskCd": update.task_code,
            "dclDe": kra_date(update.declaration_date),
            "itemSeq": update.item_seq,
            "hsCd": update.hs_code,
            "itemClsCd": update.item_class_code,
            "itemCd": update.item_code,
            # Spelling from the specification's JSON sample.
            "imptItemSttsCd": update.status,
            "remark": _cut(update.remark, 400),
            "modrId": update.operator.id[:20],
            "modrNm": update.operator.name[:60],
        })


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _purchase_line(row):
    return {
        "seq": _int(first(row, "itemSeq")),
        "item_class_code": first(row, "itemClsCd"),
        "item_code": first(row, "itemCd"),
        "name": first(row, "itemNm"),
        "barcode": first(row, "bcd"),
        "packaging_unit": first(row, "pkgUnitCd"),
        "package_quantity": first(row, "pkg"),
        "quantity_unit": first(row, "qtyUnitCd"),
        "quantity": first(row, "qty"),
        "unit_price": first(row, "prc"),
        "supply_amount": first(row, "splyAmt"),
        "discount_rate": first(row, "dcRt"),
        "discount_amount": first(row, "dcAmt"),
        "tax_type": first(row, "taxTyCd"),
        "taxable_amount": first(row, "taxblAmt"),
        "tax_amount": first(row, "taxAmt"),
        "total_amount": first(row, "totAmt"),
    }
