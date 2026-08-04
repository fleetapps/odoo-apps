# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""OAuth callback: the three checks Shopify's docs require, each proven to
reject on its own, and none of them able to leave a token behind.

The callback is the only place a store becomes trusted, so every failure mode
here has to end with access_token still empty - a test that only asserts the
HTTP status would pass while happily storing a forged token.
"""
import hashlib
import hmac
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged

SECRET = "app-secret-under-test"


@tagged("post_install", "-at_install")
class TestOAuthCallback(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1)
        cls.instance = cls.env["shopify.bisync.instance"].create({
            "name": "OAuth Store",
            "shop_url": "oauth-store.myshopify.com",
            "client_id": "test-client-id",
            "client_secret": SECRET,
            "warehouse_id": warehouse.id,
        })

    def _arm(self, state="the-expected-state"):
        """Put the store in the state action_connect_shopify would leave it."""
        self.instance.write({"oauth_state": state, "access_token": False})
        return state

    def _callback(self, params, secret=SECRET, sign=True):
        if sign:
            message = "&".join(f"{k}={params[k]}" for k in sorted(params))
            params = dict(params, hmac=hmac.new(
                secret.encode(), message.encode(), hashlib.sha256).hexdigest())
        self.authenticate("admin", "admin")
        return self.url_open("/shopify_bisync/oauth/callback", params=params)

    def _base_params(self, state):
        return {"code": "abc123", "shop": self.instance.shop_url,
                "state": state, "timestamp": "1337178173"}

    # -- rejections --------------------------------------------------------
    def test_01_bad_hmac_is_rejected(self):
        """A correct state with a signature from the wrong secret is refused."""
        state = self._arm()
        self._callback(self._base_params(state), secret="not-the-secret")
        self.assertFalse(self.instance.access_token,
                         "a forged signature must never yield a token")

    def test_02_unknown_state_is_rejected(self):
        """A perfectly signed reply for a state we never issued is refused."""
        self._arm()
        params = self._base_params("a-state-we-never-issued")
        self._callback(params)
        self.assertFalse(self.instance.access_token)

    def test_03_shop_mismatch_is_rejected(self):
        """Right state, right signature, but a different shop than the record."""
        state = self._arm()
        params = dict(self._base_params(state), shop="attacker.myshopify.com")
        self._callback(params)
        self.assertFalse(self.instance.access_token)

    def test_04_non_shopify_domain_is_rejected(self):
        state = self._arm()
        params = dict(self._base_params(state),
                      shop="oauth-store.myshopify.com.evil.test")
        self._callback(params)
        self.assertFalse(self.instance.access_token)

    def test_05_non_ascii_hmac_does_not_crash(self):
        """compare_digest raises TypeError on non-ASCII str; must not 500."""
        state = self._arm()
        params = dict(self._base_params(state), hmac="ü" * 8)
        response = self._callback(params, sign=False)
        self.assertNotEqual(response.status_code, 500)
        self.assertFalse(self.instance.access_token)

    # -- success -----------------------------------------------------------
    def test_06_valid_callback_stores_token_and_clears_state(self):
        state = self._arm()

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"access_token": "shpat_from_oauth",
                        "scope": "read_products"}

        # Only the token exchange is faked; the signature checks above it run
        # for real. The post-connect calls would hit the network, so they are
        # neutralised - their failure is already non-fatal by design.
        with patch("odoo.addons.shopify_bisync.controllers.oauth.requests.post",
                   return_value=_Resp()), \
             patch.object(type(self.instance), "action_test_connection",
                          lambda self: True), \
             patch.object(type(self.instance), "action_register_webhooks",
                          lambda self: True), \
             patch.object(type(self.instance), "action_fetch_locations",
                          lambda self: True):
            self._callback(self._base_params(state))

        self.instance.invalidate_recordset()
        self.assertEqual(self.instance.access_token, "shpat_from_oauth")
        self.assertFalse(self.instance.oauth_state,
                         "the nonce must be single-use")
        self.assertEqual(self.instance.webhook_secret, SECRET,
                         "webhook signing uses the app secret, set for the user")
        self.assertTrue(self.instance.is_connected)

    # -- guards before we ever leave for Shopify ---------------------------
    def test_07_connect_requires_credentials(self):
        self.instance.write({"client_id": False, "client_secret": False})
        with self.assertRaises(UserError):
            self.instance.action_connect_shopify()

    def test_08_connect_rejects_a_non_shopify_address(self):
        self.instance.write({"shop_url": "example.com"})
        with self.assertRaises(UserError):
            self.instance.action_connect_shopify()
