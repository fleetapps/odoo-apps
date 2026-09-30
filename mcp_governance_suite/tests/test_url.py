# -*- coding: utf-8 -*-
"""Working out the public origin.

This is the module's highest-consequence piece of logic that nobody looks at:
one wrong scheme in the OAuth metadata and every AI client refuses to connect,
with an error message that never mentions a scheme. The tests below pin the
behaviour that costs support tickets - a TLS-terminating proxy that announces
itself only through a forwarded header, and an ``http://`` value that Odoo
itself keeps writing back into ``web.base.url``.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..models import mcp_url
from ..models.mcp_url import forwarded_scheme, is_loopback, public_base_url


class FakeRequest:
    """Just enough werkzeug request for the origin logic."""

    def __init__(self, host="erp.example.com", scheme="http", headers=None):
        self.host = host
        self.scheme = scheme
        self.headers = headers or {}


class FakeOdooRequest:
    def __init__(self, httprequest):
        self.httprequest = httprequest


@tagged("post_install", "-at_install")
class TestForwardedScheme(TransactionCase):
    """Reading the scheme back out of whatever the proxy in front chose to send."""

    def _scheme(self, **headers):
        return forwarded_scheme(FakeRequest(headers=headers))

    def test_no_headers_means_no_opinion(self):
        self.assertIsNone(self._scheme())

    def test_x_forwarded_proto(self):
        self.assertEqual(self._scheme(**{"X-Forwarded-Proto": "https"}), "https")

    def test_rfc7239_forwarded_header(self):
        self.assertEqual(
            self._scheme(**{"Forwarded": 'for=203.0.113.1;proto=https;host=x'}),
            "https")

    def test_proxy_chain_reports_the_client_hop_not_the_last(self):
        """Each proxy appends, so the original client's scheme is left-most.

        Reading the right-most value reports the last internal hop, which is
        plain http on every TLS-terminating setup - the exact wrong answer.
        """
        self.assertEqual(
            self._scheme(**{"X-Forwarded-Proto": "https, http"}), "https")

    def test_cloudflare_visitor_header(self):
        """CF sends this and no X-Forwarded-Host, which is what switches
        Odoo's own ProxyFix off and leaves the scheme wrong."""
        self.assertEqual(
            self._scheme(**{"CF-Visitor": '{"scheme":"https"}'}), "https")

    def test_malformed_cf_visitor_is_not_an_opinion(self):
        self.assertIsNone(self._scheme(**{"CF-Visitor": "not json"}))

    def test_ssl_offload_conventions(self):
        self.assertEqual(self._scheme(**{"X-Forwarded-Ssl": "on"}), "https")
        self.assertEqual(self._scheme(**{"Front-End-Https": "on"}), "https")


@tagged("post_install", "-at_install")
class TestPublicBaseUrl(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Param = self.env["ir.config_parameter"].sudo()
        self.Param.set_param(mcp_url.PARAM_PUBLIC_BASE_URL, "")

    def _resolve(self, request=None):
        with patch("odoo.http.request", request):
            return public_base_url(self.env)

    # ------------------------------------------------------------ the bug
    def test_forwarded_https_beats_an_http_web_base_url(self):
        """The failure this whole module exists to prevent.

        Odoo rewrites web.base.url to wherever an admin last signed in from,
        and behind a proxy that Odoo's ProxyFix ignores that value is http. If
        the configured value won, every client would be handed an http URL and
        refuse it - and it would come back on the next admin login.
        """
        self.Param.set_param("web.base.url", "http://erp.example.com")
        request = FakeOdooRequest(FakeRequest(
            host="erp.example.com", scheme="http",
            headers={"X-Forwarded-Proto": "https"}))
        self.assertEqual(self._resolve(request), "https://erp.example.com")

    def test_configured_https_fixes_a_proxy_that_announces_nothing(self):
        """No forwarded header at all: the configured address supplies the scheme."""
        self.Param.set_param("web.base.url", "https://erp.example.com")
        request = FakeOdooRequest(FakeRequest(
            host="erp.example.com", scheme="http"))
        self.assertEqual(self._resolve(request), "https://erp.example.com")

    def test_nothing_can_downgrade_a_tls_request_to_http(self):
        self.Param.set_param("web.base.url", "http://erp.example.com")
        request = FakeOdooRequest(FakeRequest(
            host="erp.example.com", scheme="https",
            headers={"X-Forwarded-Proto": "http"}))
        self.assertEqual(self._resolve(request), "https://erp.example.com")

    # --------------------------------------------------------- other hosts
    def test_a_different_host_is_not_overridden_by_web_base_url(self):
        """Multi-domain: the issuer must be the origin the client actually used.

        Answering with web.base.url's host would make the OAuth `iss` disagree
        with the URL the client called, which strict clients reject as a
        mix-up attack.
        """
        self.Param.set_param("web.base.url", "https://erp.example.com")
        request = FakeOdooRequest(FakeRequest(
            host="second.example.com", scheme="https"))
        self.assertEqual(self._resolve(request), "https://second.example.com")

    def test_plain_http_lan_deployment_is_left_alone(self):
        """Never invent https nothing reported: the URL would not answer."""
        self.Param.set_param("web.base.url", "")
        request = FakeOdooRequest(FakeRequest(host="10.0.0.5:8069", scheme="http"))
        self.assertEqual(self._resolve(request), "http://10.0.0.5:8069")

    def test_override_wins_over_everything(self):
        self.Param.set_param(mcp_url.PARAM_PUBLIC_BASE_URL, "https://pinned.example.com/")
        self.Param.set_param("web.base.url", "http://erp.example.com")
        request = FakeOdooRequest(FakeRequest(host="erp.example.com", scheme="http"))
        self.assertEqual(self._resolve(request), "https://pinned.example.com")

    def test_outside_a_request_it_falls_back_to_the_configured_address(self):
        """Crons and tests have no proxy headers to read."""
        self.Param.set_param("web.base.url", "https://erp.example.com/")
        self.assertEqual(self._resolve(None), "https://erp.example.com")

    # ------------------------------------------------------ host injection
    def test_x_forwarded_host_is_ignored_without_proxy_mode(self):
        """Without a proxy in front, that header is caller-controlled: honouring
        it would let anyone choose which host our OAuth metadata advertises."""
        self.Param.set_param("web.base.url", "")
        request = FakeOdooRequest(FakeRequest(
            host="erp.example.com", scheme="https",
            headers={"X-Forwarded-Host": "evil.example"}))
        with patch.object(mcp_url, "config", {"proxy_mode": False}):
            self.assertEqual(self._resolve(request), "https://erp.example.com")

    def test_x_forwarded_host_is_honoured_under_proxy_mode(self):
        self.Param.set_param("web.base.url", "")
        request = FakeOdooRequest(FakeRequest(
            host="10.0.0.5:8069", scheme="https",
            headers={"X-Forwarded-Host": "erp.example.com"}))
        with patch.object(mcp_url, "config", {"proxy_mode": True}):
            self.assertEqual(self._resolve(request), "https://erp.example.com")


@tagged("post_install", "-at_install")
class TestLoopback(TransactionCase):

    def test_loopback_hosts(self):
        for host in ("localhost", "localhost:8069", "127.0.0.1", "::1",
                     "[::1]", "[::1]:8069"):
            self.assertTrue(is_loopback(host), host)

    def test_public_hosts_are_not_loopback(self):
        for host in ("erp.example.com", "erp.example.com:443", "",
                     "localhost.attacker.example"):
            self.assertFalse(is_loopback(host), host)


@tagged("post_install", "-at_install")
class TestBaseUrlDrift(TransactionCase):
    """The address is right today and Odoo resets it tomorrow."""

    def setUp(self):
        super().setUp()
        self.Param = self.env["ir.config_parameter"].sudo()
        self.Param.set_param(mcp_url.PARAM_PUBLIC_BASE_URL, "")
        self.Connect = self.env["mcp.connect"]

    def _checks(self):
        return {c["key"]: c for c in self.Connect.get_state()["checks"]}

    def test_http_web_base_url_behind_https_is_reported_as_drift(self):
        self.Param.set_param("web.base.url", "http://erp.example.com")
        self.Param.set_param("web.base.url.freeze", "")
        request = FakeOdooRequest(FakeRequest(
            host="erp.example.com", scheme="http",
            headers={"X-Forwarded-Proto": "https"}))
        with patch("odoo.http.request", request):
            checks = self._checks()
        # The address itself is now correct...
        self.assertEqual(checks["base_url"]["state"], "ok")
        # ...but it is one admin login away from being reset.
        self.assertEqual(checks["base_url_drift"]["state"], "warn")

    def test_drift_warning_does_not_block_connecting(self):
        """A warning must never read as a blocker - the server does work."""
        self.Param.set_param("web.base.url", "http://erp.example.com")
        self.Param.set_param("web.base.url.freeze", "")
        request = FakeOdooRequest(FakeRequest(
            host="erp.example.com", scheme="http",
            headers={"X-Forwarded-Proto": "https"}))
        with patch("odoo.http.request", request):
            checks = self.Connect.get_state()["checks"]
        self.assertNotIn("fail", [c["state"] for c in checks
                                  if c["key"] == "base_url_drift"])

    def test_freezing_silences_the_drift_warning(self):
        self.Param.set_param("web.base.url", "https://erp.example.com")
        self.Param.set_param("web.base.url.freeze", "True")
        self.assertNotIn("base_url_drift", self._checks())

    def test_fix_pins_and_freezes_the_address(self):
        self.Param.set_param("web.base.url", "http://erp.example.com")
        self.Param.set_param("web.base.url.freeze", "")
        request = FakeOdooRequest(FakeRequest(
            host="erp.example.com", scheme="http",
            headers={"X-Forwarded-Proto": "https"}))
        with patch("odoo.http.request", request):
            self.Connect.fix_base_url()
        self.assertEqual(self.Param.get_param("web.base.url"),
                         "https://erp.example.com")
        self.assertTrue(self.Param.get_param("web.base.url.freeze"))
