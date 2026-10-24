# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Public endpoints Safaricom calls.

``/pay/ke/<token>/<kind>``: no "mpesa", "safaricom", "exe", "cmd", "sql" or
"query" anywhere, because Daraja refuses those, and a random hex token per
shortcode, because callbacks are not signed. ``type="http"`` with the raw body
parsed here: Safaricom posts plain JSON, not JSON-RPC.

The database is the one Odoo resolves for the request's host name (dbfilter,
or a single database). Safaricom sends neither cookies nor an X-Odoo-Database
header, which is why the shortcode form warns when the callback host name
does not lead to its database alone.
"""

import logging

from odoo import SUPERUSER_ID, http
from odoo.http import request

from ..lib.log_filter import mask_tokens
from ..models.mpesa_shortcode import CALLBACK_PATHS
from ..models.mpesa_callback import MAX_BODY

_logger = logging.getLogger(__name__)

KIND_BY_PATH = {path: kind for kind, path in CALLBACK_PATHS.items()}


class MpesaCallbackController(http.Controller):

    @http.route("/pay/ke/<string:token>/<path:subpath>", type="http", auth="none",
                methods=["POST"], csrf=False, save_session=False, readonly=False)
    def mpesa_callback(self, token, subpath, **_kwargs):
        kind = KIND_BY_PATH.get(subpath.strip("/"))
        if not kind:
            return request.make_json_response({"ResultCode": 1, "ResultDesc": "Unknown"},
                                              status=404)
        body = request.httprequest.get_data(cache=False)
        if len(body) > MAX_BODY:
            _logger.warning("M-Pesa callback too large (%s bytes) on %s",
                            len(body), mask_tokens(request.httprequest.path))
            return request.make_json_response({"ResultCode": 1, "ResultDesc": "Too large"},
                                              status=413)
        remote_ip = request.httprequest.remote_addr
        # auth="none" has no user: everything below runs as the superuser, and
        # the queued job with it.
        env = request.env(user=SUPERUSER_ID, su=True)
        answer = env["mpesa.callback"]._receive(kind, token, body, remote_ip)
        if answer is None:
            return request.make_json_response({"ResultCode": 1, "ResultDesc": "Unknown"},
                                              status=404)
        return request.make_json_response(answer)
