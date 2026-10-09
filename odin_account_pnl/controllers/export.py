"""Download of the P&L as PDF or Excel.

The client posts its options with ``download`` (@web/core/network/download),
which adds the CSRF token; the report is computed again here, as the user, so
nothing in the file comes from the browser but the choices.
https://www.odoo.com/documentation/19.0/developer/reference/backend/http.html
"""

import json

from odoo import http
from odoo.http import content_disposition, request


class OdinPnlExportController(http.Controller):

    @http.route("/odin_account_pnl/export", type="http", auth="user", methods=["POST"])
    def export(self, options="{}", fmt="xlsx", **kwargs):
        request.env["odin.pnl.report"]._check_access()
        content, filename, mimetype = request.env["odin.pnl.export"].render(
            json.loads(options or "{}"), "xlsx" if fmt == "xlsx" else "pdf")
        return request.make_response(content, headers=[
            ("Content-Type", mimetype),
            ("Content-Length", len(content)),
            ("Content-Disposition", content_disposition(filename)),
        ])
