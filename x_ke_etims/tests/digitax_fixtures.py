# -*- coding: utf-8 -*-
"""A stand-in for DigiTax's API, for the tests.

Routes and headers are the ones DigiTax documents (base /ke/v2, X-API-Key).
The JSON bodies follow the field names in ``backends/digitax.py``; when those
are aligned with DigiTax's reference, these fixtures move with them.
"""

import json
import re
from urllib.parse import urlparse

import requests

ORIGINAL_REQUEST = requests.Session.request

SALE_SIGNED = {
    "receipt_number": 14,
    "total_receipt_number": 52,
    "internal_data": "TE68SLA234J5EAV3N56988LJQ7",
    "receipt_signature": "V249J39CFJ48HE2W",
    "scu_id": "KRACU0100000001",
    "mrc_no": "WIS00000001",
    "signed_at": "20261024110735",
    "etims_url": "https://etims.kra.go.ke/common/link/etims/receipt/"
                 "indexEtimsReceiptData?Data=P051234567X00V249J39CFJ48HE2W",
}


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.content = json.dumps(body).encode() if body is not None else b""
        self.text = self.content.decode()

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeDigiTax:
    """Routes ``requests.Session.request`` to canned DigiTax answers."""

    def __init__(self, api_key="test-key"):
        self.api_key = api_key
        self.calls = []
        self.sales = {}
        self.items = {}
        self.sale_outcome = "PENDING"
        self.fail = {}

    def __call__(self, session, method, url, data=None, headers=None, params=None,
                 timeout=None, **kwargs):
        if urlparse(url).hostname in ("127.0.0.1", "localhost"):
            # The test's own calls to Odoo's HTTP server.
            return ORIGINAL_REQUEST(session, method, url, data=data, headers=headers,
                                    params=params, timeout=timeout, **kwargs)
        path = urlparse(url).path
        path = path[path.index("/ke/v2") + len("/ke/v2"):] if "/ke/v2" in path else path
        body = json.loads(data) if data else None
        self.calls.append((method, path, body, dict(headers or {})))
        if (headers or {}).get("X-API-Key") != self.api_key:
            return FakeResponse(401, {"message": "Invalid API key"})
        if (method, path) in self.fail:
            status, answer = self.fail.pop((method, path))
            if status is None:
                raise requests.ConnectionError("connection reset by peer")
            return FakeResponse(status, answer)
        return self.route(method, path, body)

    def route(self, method, path, body):
        if (method, path) == ("GET", "/etims-info"):
            return FakeResponse(200, {"data": {
                "pin": "P051234567X", "branch_id": "00", "business_name": "Nairobi Traders Ltd",
                "scu_id": "KRACU0100000001", "mrc_no": "WIS00000001"}})
        if (method, path) == ("GET", "/branches"):
            return FakeResponse(200, {"data": [
                {"branch_id": "00", "name": "Head Office", "is_head_office": True},
                {"branch_id": "01", "name": "Kisumu"}]})
        if (method, path) == ("GET", "/notices"):
            return FakeResponse(200, [{"id": 7, "title": "System maintenance",
                                       "content": "Sunday 02:00-04:00", "created_at":
                                       "2026-10-20 08:00:00"}])
        if (method, path) == ("POST", "/items"):
            item_id = "itm_%s" % (len(self.items) + 1)
            self.items[item_id] = body
            return FakeResponse(201, {"id": item_id, "status": "PENDING"})
        match = re.match(r"^/items/(\w+)$", path)
        if match and method in ("PUT", "GET"):
            if method == "PUT":
                self.items[match.group(1)] = body
            return FakeResponse(200, {"id": match.group(1), "status": "COMPLETED"})
        if (method, path) in (("POST", "/sales"), ("POST", "/credit-notes")):
            sale_id = "sal_%s" % (len(self.sales) + 1)
            self.sales[sale_id] = {"request": body, "status": self.sale_outcome}
            return FakeResponse(201, self.sale_answer(sale_id))
        match = re.match(r"^/sales/(\w+)$", path)
        if match and method == "GET":
            if match.group(1) not in self.sales:
                return FakeResponse(404, {"message": "Not found"})
            return FakeResponse(200, self.sale_answer(match.group(1)))
        if (method, path) == ("POST", "/reverse-invoices"):
            return FakeResponse(201, {"id": "rev_1", "status": "COMPLETED", **SALE_SIGNED})
        return FakeResponse(404, {"message": "No route %s %s" % (method, path)})

    def sale_answer(self, sale_id):
        sale = self.sales[sale_id]
        answer = {"id": sale_id, "status": sale["status"]}
        if sale["status"] == "COMPLETED":
            answer.update(SALE_SIGNED)
        if sale["status"] == "FAILED":
            answer["error_message"] = "Item not synced with KRA"
        return answer

    def complete(self, sale_id, status="COMPLETED"):
        self.sales[sale_id]["status"] = status
