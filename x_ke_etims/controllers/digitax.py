# -*- coding: utf-8 -*-
"""DigiTax's notifications: item.sync and sale.sync.

DigiTax does not sign its callbacks, so nothing in one is believed. The
address carries a per-device token, which keeps strangers from making us
fetch at will, and the payload is read only for *which* item or sale changed;
that record is then fetched from DigiTax with our own key, and only what
DigiTax says on that call is stored.
"""

import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class KeEtimsDigitaxController(http.Controller):

    @http.route("/x_ke_etims/digitax/<int:device_id>/<string:token>", type="http",
                auth="public", methods=["POST"], csrf=False, save_session=False)
    def digitax_callback(self, device_id, token, **kwargs):
        device = request.env["ke.etims.device"].sudo().browse(device_id).exists()
        if not device or device.backend != "digitax" or not device.callback_token \
                or not hmac.compare_digest(device.callback_token, token):
            return request.make_json_response({"ok": False}, status=404)
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return request.make_json_response({"ok": False, "error": "not JSON"}, status=400)
        try:
            request.env["ke.etims.document"].sudo()._ke_etims_handle_callback(device, payload)
        except Exception:
            _logger.exception("eTIMS: DigiTax callback for device %s failed", device_id)
            return request.make_json_response({"ok": False}, status=500)
        return request.make_json_response({"ok": True})
