# -*- coding: utf-8 -*-
# Part of Fleet FMS Connector. License OPL-1.
"""FMS -> Odoo API.

Controllers ref: https://www.odoo.com/documentation/19.0/developer/reference/backend/http.html

Security: every call carries ``X-FMS-Api-Key``, compared in constant time
against ``fms_connector.inbound_api_key`` (Settings > Fleet FMS Connector).
No signature-over-body scheme like the Shopify webhook controller uses --
FMS is a trusted server-to-server caller with a shared secret, not a public
webhook source with its own signing scheme to verify.

Two routes:
- POST /fms_connector/api/v1/purchase_requests -- a driver's purchase
  request. Creates/updates a purchase.request; RFQ, bid analysis and PO
  approval happen afterwards entirely inside Odoo, unchanged.
- POST /fms_connector/api/v1/expenses -- a completed job card + invoice.
  Creates/updates a draft hr.expense for approval inside Odoo.

Both are upserts keyed on the FMS-supplied ``fms_ref``, so a retried call
after a network failure does not create a duplicate.
"""
import json
import logging
import secrets

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


class FmsConnectorApi(http.Controller):

    def _check_api_key(self):
        expected = request.env["ir.config_parameter"].sudo().get_param(
            "fms_connector.inbound_api_key"
        )
        provided = request.httprequest.headers.get("X-FMS-Api-Key", "")
        return bool(expected) and secrets.compare_digest(expected, provided)

    def _json_error(self, message, status=400):
        return request.make_json_response({"error": message}, status=status)

    @http.route("/fms_connector/api/v1/purchase_requests", type="http",
                auth="public", methods=["POST"], csrf=False,
                save_session=False)
    def create_purchase_request(self, **kw):
        if not self._check_api_key():
            return self._json_error("invalid or missing X-FMS-Api-Key", 401)
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return self._json_error("invalid JSON body", 400)

        try:
            pr = request.env["purchase.request"].sudo().fms_upsert(payload)
        except UserError as exc:
            _logger.warning("fms_connector: purchase request push rejected: %s", exc)
            return self._json_error(str(exc), 422)
        except Exception:
            _logger.exception("fms_connector: purchase request push failed")
            return self._json_error("internal error", 500)

        return request.make_json_response(pr.fms_status_payload(), status=201)

    @http.route("/fms_connector/api/v1/expenses", type="http",
                auth="public", methods=["POST"], csrf=False,
                save_session=False)
    def create_expense(self, **kw):
        if not self._check_api_key():
            return self._json_error("invalid or missing X-FMS-Api-Key", 401)
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return self._json_error("invalid JSON body", 400)

        try:
            expense = request.env["hr.expense"].sudo().fms_upsert(payload)
        except UserError as exc:
            _logger.warning("fms_connector: expense push rejected: %s", exc)
            return self._json_error(str(exc), 422)
        except Exception:
            _logger.exception("fms_connector: expense push failed")
            return self._json_error("internal error", 500)

        return request.make_json_response(expense.fms_status_payload(), status=201)
