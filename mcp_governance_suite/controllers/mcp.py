# -*- coding: utf-8 -*-
"""MCP endpoint (Streamable HTTP transport, JSON-RPC 2.0 messages).

Odoo Web Controllers reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/http.html
MCP spec (verify against latest revision at build time):
https://modelcontextprotocol.io/specification

Auth: Bearer API key -> mcp.api.key (sha256-hashed at rest). The request is
then executed as the key's Odoo user via request.update_env(user=...), so
ir.model.access and ir.rule apply on top of MCP scopes - defense in depth.
"""
import hashlib
import json
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)
PROTOCOL_VERSION = "2025-03-26"  # VERIFY-ON-BUILD against current MCP spec


class MCPController(http.Controller):

    def _authenticate(self):
        auth = request.httprequest.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        key_hash = hashlib.sha256(auth[7:].encode()).hexdigest()
        key = request.env["mcp.api.key"].sudo().search(
            [("key_hash", "=", key_hash), ("active", "=", True)], limit=1)
        if not key or key.is_expired():
            return None
        request.update_env(user=key.user_id.id)
        return key

    def _rpc_error(self, id_, code, message):
        return request.make_json_response(
            {"jsonrpc": "2.0", "id": id_,
             "error": {"code": code, "message": message}})

    @http.route("/mcp/v1", type="http", auth="none", methods=["POST"],
                csrf=False, save_session=False)
    def mcp_endpoint(self, **kw):
        key = self._authenticate()
        if not key:
            return request.make_json_response(
                {"error": "unauthorized"}, status=401)
        try:
            msg = json.loads(request.httprequest.get_data())
        except Exception:
            return self._rpc_error(None, -32700, "Parse error")

        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params", {})
        Engine = request.env["mcp.engine"]

        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": "odoo-mcp-governance", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            }
        elif method == "tools/list":
            result = {"tools": Engine.list_tools(key)}
        elif method == "tools/call":
            result = Engine.call_tool(
                key, params.get("name"), params.get("arguments", {}))
        elif method in ("ping", "notifications/initialized"):
            result = {}
        else:
            return self._rpc_error(mid, -32601, f"Method not found: {method}")

        return request.make_json_response(
            {"jsonrpc": "2.0", "id": mid, "result": result})
