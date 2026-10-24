# -*- coding: utf-8 -*-
"""A signer that signs nothing, for sales demos and tests.

Every answer is derived from its input, so the same invoice gets the same
"signature" on every run and a test can assert on it. Nothing leaves the
server. The invoice PDF and the POS receipt carry "DEMO - NOT A TAX INVOICE"
whenever a demo device signed them, because a convincing fake receipt is
exactly what must never reach a customer.

The code list it serves is the handful of KRA code classes the module's own
flows need, with the values the OSCU specification v2.0 publishes for them.
It is not KRA's full list and does not pretend to be.
"""

import base64
import hashlib
from datetime import datetime, timezone

from .base import Adapter, DeviceInfo, Receipt, register

DEMO_RECEIPT_URL = "https://etims-sbx.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData"

DEMO_CODES = [
    ("04", "Taxation Type", [
        ("A", "A-Exempt", "0"), ("B", "B-16.00%", "16"), ("C", "C-0%", "0"),
        ("D", "D-Non-VAT", "0"), ("E", "E-8%", "8"),
    ]),
    ("05", "Nation", [("KE", "KENYA", None)]),
    ("07", "Payment Type", [
        ("01", "CASH", None), ("02", "CREDIT", None), ("03", "CASH/CREDIT", None),
        ("04", "BANK CHECK", None), ("05", "DEBIT&CREDIT CARD", None),
        ("06", "MOBILE MONEY", None), ("07", "OTHER", None),
    ]),
    ("10", "Quantity Unit", [("U", "Pieces/item", None), ("KG", "Kilo-Gramme", None),
                             ("L", "Litre", None)]),
    ("17", "Packaging Unit", [("NT", "Net", None), ("CT", "Carton", None),
                              ("BG", "Bag", None)]),
    ("32", "Credit Note Reason", [
        ("01", "Missing Quantity", None), ("02", "Missing data", None),
        ("03", "Damaged", None), ("04", "Wasted", None),
        ("05", "Raw Material Shortage", None), ("06", "Refund", None),
    ]),
]

DEMO_ITEM_CLASSES = [
    ("8111180000", "IT consulting services (demo)", "B"),
    ("5059690800", "Packaged food (demo)", "B"),
    ("1010150000", "Live animals (demo)", "A"),
]


def _token(*parts, length):
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return base64.b32encode(digest).decode()[:length]


@register
class DemoAdapter(Adapter):
    code = "demo"
    label = "Demo"
    environments = ("demo",)

    def initialize(self):
        device = self.device
        tin = device.tin or "P000000000Z"
        self.record("initialize", {"tin": tin, "bhfId": device.bhf_id},
                    {"resultCd": "000", "resultMsg": "Demo device"})
        return DeviceInfo(
            tin=tin,
            bhf_id=device.bhf_id,
            taxpayer_name=device.company_id.name,
            branch_name=device.company_id.name,
            scu_id="KRACU0DEMO" + _token(tin, device.bhf_id, length=5),
            mrc_no="DEMO" + _token(device.bhf_id, length=6),
            device_id="DEMO" + _token(device.serial or "", length=8),
            cmc_key=None,
        )

    def sync_codes(self, since=None):
        return [
            {
                "class": cls,
                "name": name,
                "codes": [
                    {"code": code, "name": label, "description": label,
                     "sort_order": order, "active": True,
                     "user_defined": (extra, None, None)}
                    for order, (code, label, extra) in enumerate(codes, start=1)
                ],
            }
            for cls, name, codes in DEMO_CODES
        ]

    def sync_item_classes(self, since=None):
        return [
            {"code": code, "name": name, "level": 5, "tax_type": tax,
             "major_target": False, "active": True}
            for code, name, tax in DEMO_ITEM_CLASSES
        ]

    def fetch_branches(self):
        return [{"bhf_id": "00", "name": self.device.company_id.root_id.name,
                 "status": "01", "is_head_office": True, "raw": {}}]

    def fetch_notices(self, since=None):
        return []

    def register_item(self, item):
        self.record("register_item", {"itemCd": item.item_code}, {"resultCd": "000"})
        return None

    def sign_sale(self, sale):
        return self._sign(sale)

    def sign_credit_note(self, sale):
        return self._sign(sale)

    def _sign(self, sale):
        device = self.device
        totals = sale.totals()
        signature = _token("sign", device.tin, device.bhf_id, sale.invoice_no,
                           totals.total_amount, length=16)
        internal = _token("intrl", device.tin, device.bhf_id, sale.invoice_no,
                          totals.total_tax, length=26)
        tin = device.tin or "P000000000Z"
        receipt = Receipt(
            status="signed",
            kra_invoice_no=sale.invoice_no,
            receipt_no=sale.invoice_no,
            total_receipt_no=sale.invoice_no,
            internal_data=internal,
            signature=signature,
            scu_id=device.scu_id,
            mrc_no=device.mrc_no,
            signed_at=datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0),
            qr_url="%s?Data=%s%s%s" % (DEMO_RECEIPT_URL, tin, device.bhf_id, signature),
            provider_ref="DEMO-%s-%s" % (device.bhf_id, sale.invoice_no),
        )
        self.record("sign", {"invcNo": sale.invoice_no, "rcptTyCd": sale.receipt_type,
                             "totAmt": str(totals.total_amount)},
                    {"resultCd": "000", "rcptSign": signature})
        return receipt

    def fetch_purchases(self, since=None):
        return []

    def confirm_purchase(self, purchase):
        self.record("confirm_purchase", {"invcNo": purchase.invoice_no},
                    {"resultCd": "000"})

    def report_stock_io(self, stock_io):
        self.record("report_stock_io", {"sarNo": stock_io.sar_no,
                                        "sarTyCd": stock_io.io_type},
                    {"resultCd": "000"})

    def save_stock_master(self, stock_master):
        self.record("save_stock_master", {"itemCd": stock_master.item_code,
                                          "rsdQty": str(stock_master.remaining_quantity)},
                    {"resultCd": "000"})

    def fetch_imports(self, since=None):
        return []

    def update_import(self, update):
        self.record("update_import", {"taskCd": update.task_code}, {"resultCd": "000"})

    def save_item_composition(self, composition):
        self.record("save_item_composition", {"itemCd": composition.item_code},
                    {"resultCd": "000"})
