# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Shopify OAuth callback - the merchant's way back from the approval screen.

Flow: instance.action_connect_shopify() sends the merchant to Shopify's
consent page; Shopify sends them here with a one-time ``code``; we swap that
for a permanent offline access token and store it. The merchant types nothing.

https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/authorization-code-grant
Odoo controllers ref:
https://www.odoo.com/documentation/19.0/developer/reference/backend/http.html

Security - all three checks Shopify's docs require, in order, before the code
is ever exchanged:
  1. ``shop`` matches the documented myshopify.com pattern;
  2. ``hmac`` verifies as a hex HMAC-SHA256 over the other query parameters,
     sorted and joined, keyed by the app secret (note: hex here, unlike the
     base64 body digest on inbound webhooks);
  3. ``state`` equals the nonce this record generated, compared in constant
     time and cleared immediately so a code cannot be replayed.
"""
import hashlib
import hmac
import logging

import requests

from odoo import _, http
from odoo.http import request

from ..models.instance import SHOP_DOMAIN_RE

_logger = logging.getLogger(__name__)


class ShopifyOAuth(http.Controller):

    def _fail(self, instance_id, message):
        """Land the merchant back on the store with a readable explanation.

        Deliberately never echoes the query string back into the page - it
        carries the authorization code.
        """
        _logger.warning("Shopify OAuth callback rejected: %s", message)
        if instance_id:
            request.env["shopify.bisync.instance"].sudo().browse(
                instance_id).exists().message_post(
                    body=_("Shopify connection failed: %s", message))
        return request.render("shopify_bisync.oauth_error",
                              {"message": message, "instance_id": instance_id})

    @http.route("/shopify_bisync/oauth/callback", type="http", auth="user",
                methods=["GET"], csrf=False, save_session=False)
    def callback(self, **params):
        Instance = request.env["shopify.bisync.instance"].sudo()
        shop = params.get("shop", "")
        state = params.get("state", "")
        code = params.get("code", "")

        if not SHOP_DOMAIN_RE.match(shop):
            return self._fail(None, _("%s is not a Shopify store address.", shop))

        instance = Instance.search([("oauth_state", "=", state)], limit=1) \
            if state else Instance.browse()
        if not instance:
            return self._fail(None, _(
                "This approval could not be matched to a store awaiting "
                "connection. Press Connect to Shopify again to start over."))
        # Shopify sends the domain lowercased; a record imported rather than
        # typed may not be, so do not let casing alone reject a valid reply.
        if (instance.shop_url or "").lower() != shop.lower():
            return self._fail(instance.id, _(
                "Shopify replied for %(got)s but this store is %(want)s.",
                got=shop, want=instance.shop_url))
        # The search above found the record by state; this re-checks it
        # without the comparison being short-circuited on first difference.
        if not hmac.compare_digest(
                (instance.oauth_state or "").encode(), state.encode()):
            return self._fail(instance.id, _("This approval has expired."))

        # HMAC over every parameter except hmac itself, sorted, hex digest.
        # Values are the decoded ones, matching Shopify's documented example.
        rest = {k: v for k, v in params.items() if k != "hmac"}
        message = "&".join(f"{k}={rest[k]}" for k in sorted(rest))
        digest = hmac.new((instance.client_secret or "").encode(),
                          message.encode(), hashlib.sha256).hexdigest()
        # Compare as bytes: hmac.compare_digest raises TypeError on a str
        # containing non-ASCII, which a hostile caller can trivially send.
        if not instance.client_secret or not hmac.compare_digest(
                digest.encode(), (params.get("hmac") or "").encode()):
            return self._fail(instance.id, _(
                "The reply from Shopify could not be verified as genuine. "
                "Check that the App Secret matches the app you connected."))

        # Single-use: clear before the exchange so a replayed callback finds
        # nothing to match, even if the exchange itself fails.
        instance.oauth_state = False

        try:
            resp = requests.post(
                f"https://{shop}/admin/oauth/access_token",
                data={"client_id": instance.client_id,
                      "client_secret": instance.client_secret,
                      "code": code},
                headers={"Accept": "application/json"}, timeout=30)
        except requests.RequestException as exc:
            return self._fail(instance.id, _("Could not reach Shopify: %s", exc))
        if resp.status_code != 200:
            return self._fail(instance.id, _(
                "Shopify refused the connection (%(code)s). The approval link "
                "may have already been used - press Connect to Shopify again.",
                code=resp.status_code))

        body = resp.json()
        token = body.get("access_token")
        if not token:
            return self._fail(instance.id, _("Shopify returned no access token."))

        instance.write({
            "access_token": token,
            # Inbound webhooks are signed with the app secret, so the merchant
            # never has to find and copy a second value.
            "webhook_secret": instance.client_secret,
        })
        instance.message_post(body=_(
            "Connected to Shopify. Access granted for: %s",
            body.get("scope", "")))

        # Finish the setup the merchant would otherwise have to drive by hand.
        # Failures here are not connection failures: the store IS connected,
        # so report them on the record and still land on a connected store.
        for step, action in ((_("verify the connection"), "action_test_connection"),
                             (_("set up live updates"), "action_register_webhooks"),
                             (_("load store locations"), "action_fetch_locations")):
            try:
                getattr(instance, action)()
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                _logger.warning("Shopify post-connect %s failed", action,
                                exc_info=True)
                instance.message_post(body=_(
                    "Connected, but could not %(step)s automatically: "
                    "%(err)s", step=step, err=exc))

        return request.redirect(
            "/odoo/action-shopify_bisync.instance_action/%s" % instance.id)
