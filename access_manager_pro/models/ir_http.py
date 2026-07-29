# -*- coding: utf-8 -*-
"""Session-level enforcement and client hints.

``session_info`` is the single dict shipped to the web client at boot.  It is
the right place to:

* force the developer/debug flag off for a restricted user; and
* hand the client a small, per-user map of the few restrictions that can only
  be applied in the browser (archive/unarchive menu entry, the "Add a
  Property" button), consumed by the OWL patches under ``static/src/js``.

The method name is stable across Odoo 17-19.
"""

from odoo import api, models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        info = super().session_info()
        if self.env.su:
            return info
        config = self.env["access.manager.profile"]._get_access_config()
        if not config["restricted"]:
            return info
        if config["disable_developer_mode"] and info.get("debug"):
            info["debug"] = ""
        info["access_manager"] = self._am_client_hints(config)
        return info

    @api.model
    def _am_client_hints(self, config):
        """Compact, JSON-serialisable restriction map for the browser.

        Only the *global* (all-model) switches are shipped here; they are
        applied by toggling ``<body>`` classes (see access_manager.js /
        access_manager.scss). Per-model chatter switches travel on the view
        arch itself as a class on the ``<chatter/>`` node.
        """
        g = config["globals"]
        return {
            "is_readonly_user": config["is_readonly_user"],
            "hide_add_property": bool(g.get("hide_add_property")),
            "hide_spreadsheet": bool(g.get("hide_spreadsheet")),
            "hide_favourites": bool(g.get("hide_favourites")),
            "chatter": {
                "message": bool(g.get("hide_send_message")),
                "note": bool(g.get("hide_log_note")),
                "activity": bool(g.get("hide_activity")),
                "followers": bool(g.get("hide_followers")),
            },
        }
