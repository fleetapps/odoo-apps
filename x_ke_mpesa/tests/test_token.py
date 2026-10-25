# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""One token per app, shared, refreshed once when Daraja refuses it."""

from odoo.tests import tagged

from ..models import mpesa_app
from .common import MpesaCommon


@tagged("post_install", "-at_install")
class TestToken(MpesaCommon):

    def setUp(self):
        super().setUp()
        self.app._forget_token()
        self.fake.token_calls = 0

    def test_token_is_cached(self):
        self.app._token()
        self.app._token()
        self.app._token()
        self.assertEqual(self.fake.token_calls, 1)
        self.assertTrue(self.app.sudo().access_token)
        self.assertTrue(self.app.token_expires_at)

    def test_shared_cache_survives_a_new_process(self):
        self.app._token()
        # Another worker: nothing in its memory, the token in the database.
        mpesa_app._TOKENS.clear()
        self.app._token()
        self.assertEqual(self.fake.token_calls, 1)

    def test_rejected_token_is_refreshed_once(self):
        """Someone else asked Daraja for a token: ours is now invalid."""
        self.app._token()
        self.fake.tokens = ["tok-somebody-else"]
        tx = self.stk(10)
        self.assertEqual(tx.state, "requested")
        self.assertEqual(self.fake.token_calls, 2)

    def test_a_token_refused_twice_is_not_a_loop(self):
        """Every token is refused: one refresh, then the request fails rather
        than hammering the token endpoint (Daraja answers that with more
        404.001.03, not with a 429)."""
        self.app._token()
        self.fake.reject_all_tokens = True
        try:
            tx = self.stk(10)
        finally:
            self.fake.reject_all_tokens = False
        self.assertEqual(tx.state, "failed")
        self.assertEqual(self.fake.token_calls, 2)

    def test_new_credentials_drop_the_token(self):
        self.app._token()
        self.app.consumer_secret = "changed"
        self.assertFalse(self.app.sudo().access_token)

    def test_wrong_credentials_are_reported(self):
        self.app.consumer_secret = "wrong"
        with self.assertRaises(Exception):
            self.app.action_test_connection()
