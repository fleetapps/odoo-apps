# -*- coding: utf-8 -*-
"""Working out the public origin AI clients actually reach.

Every URL this module hands out - the server URL a user pastes into Claude, the
OAuth ``issuer``, the authorization and token endpoints, the RFC 9728 resource
identifier - has to be the *exact* origin the client used, over *https*. OAuth
2.1 §1.5 forbids plain-http endpoints, so a single ``http://`` in the discovery
metadata makes a compliant client refuse to connect, with an error that says
nothing about why.

Getting that origin right is harder than it looks, for three compounding
reasons:

1. ``request.httprequest.host_url`` reports the scheme the WSGI layer saw,
   which is plain http behind a TLS-terminating proxy. Odoo only corrects it
   with werkzeug's ProxyFix when ``proxy_mode`` is on **and** the proxy sends
   ``X-Forwarded-Host`` (``odoo/http.py``: ``if config['proxy_mode'] and
   environ.get("HTTP_X_FORWARDED_HOST")``). Several common front ends -
   Cloudflare among them - send ``X-Forwarded-Proto`` without
   ``X-Forwarded-Host``, so ``proxy_mode = True`` is set, looks right, and
   still leaves the scheme as http.

2. ``web.base.url`` cannot be trusted to hold the fix, because Odoo overwrites
   it with whatever address a member of ``base.group_system`` last logged in
   from, unless ``web.base.url.freeze`` is set (``res_users.py::authenticate``).
   On a deployment hitting (1) that address is itself http, so the parameter
   gets *reset to the broken value* on every admin login. That is the failure
   mode where connecting works, then silently stops working days later.

3. Neither of those is visible from inside Odoo's UI. The admin sees https in
   the browser's address bar and has no reason to suspect the server disagrees.

So the scheme is resolved from every signal available and https wins whenever
any of them reports it. The rules, in order:

* an explicit ``mcp_governance_suite.public_base_url`` override always wins -
  the escape hatch for deployments no heuristic can read;
* otherwise the host comes from the request (``X-Forwarded-Host`` only when
  ``proxy_mode`` is on, matching Odoo's own trust boundary);
* and the scheme is https if the forwarded headers say so, *or* the request
  itself arrived over TLS, *or* ``web.base.url`` names this same host over
  https. Nothing here can downgrade https to http.

Deliberately *not* done: forcing https on any non-loopback host. A client that
reached us over plain http on a private network would then be handed a URL that
does not answer. The readiness checks on the Connect screen report that case
instead, where it can be read and fixed.
"""
import json
from urllib.parse import urlparse

from odoo.tools import config

PARAM_PUBLIC_BASE_URL = "mcp_governance_suite.public_base_url"
PARAM_WEB_BASE_URL = "web.base.url"
PARAM_FREEZE = "web.base.url.freeze"

# Hosts for which plain http is legitimate: OAuth 2.1 §1.5 carves out loopback
# for native-app development, and so does every MCP client.
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

# Checked in order. The first that names a scheme wins.
FORWARDED_PROTO_HEADERS = ("X-Forwarded-Proto", "X-Forwarded-Scheme", "X-Url-Scheme")


def _first(value):
    """The left-most value of a comma-accumulated forwarded header.

    Each proxy in a chain appends, so the original client's value is first.
    """
    return (value or "").split(",")[0].strip()


def _httprequest():
    """The live werkzeug request, or None outside an HTTP context (cron, tests)."""
    try:
        from odoo.http import request
    except ImportError:  # pragma: no cover - odoo.http is always importable
        return None
    return getattr(request, "httprequest", None) if request else None


# ------------------------------------------------------------------- scheme
def forwarded_scheme(httprequest):
    """The scheme the *client* used, as reported by a terminating proxy.

    Returns "http", "https", or None when no proxy header says.
    """
    headers = httprequest.headers

    # RFC 7239, the standardised spelling: `Forwarded: for=..;proto=https`.
    for part in _first(headers.get("Forwarded")).split(";"):
        key, _, value = part.partition("=")
        if key.strip().lower() == "proto":
            value = value.strip().strip('"').lower()
            if value in ("http", "https"):
                return value

    # The de-facto headers, in the order proxies are most likely to set them.
    for header in FORWARDED_PROTO_HEADERS:
        value = _first(headers.get(header)).lower()
        if value in ("http", "https"):
            return value

    # nginx/Apache SSL-offload conventions.
    if _first(headers.get("X-Forwarded-Ssl")).lower() == "on":
        return "https"
    if _first(headers.get("Front-End-Https")).lower() == "on":
        return "https"

    # Cloudflare, which sends this but not X-Forwarded-Host - exactly the
    # combination that leaves Odoo's own ProxyFix switched off.
    visitor = headers.get("CF-Visitor")
    if visitor:
        try:
            scheme = json.loads(visitor).get("scheme", "").lower()
        except (ValueError, AttributeError):
            scheme = ""
        if scheme in ("http", "https"):
            return scheme
    return None


def _request_scheme(httprequest):
    """The scheme as the WSGI layer saw it, after any ProxyFix Odoo applied."""
    return (httprequest.scheme or "http").lower()


# --------------------------------------------------------------------- host
def _request_host(httprequest):
    """The host clients addressed, including a non-default port.

    ``X-Forwarded-Host`` is honoured only under ``proxy_mode``. Without a proxy
    in front, that header is attacker-controlled and would let a caller decide
    which host our OAuth metadata advertises.
    """
    if config.get("proxy_mode"):
        forwarded = _first(httprequest.headers.get("X-Forwarded-Host"))
        if forwarded:
            return forwarded
    return httprequest.host


def is_loopback(host):
    """True for a host[:port] that only ever names this machine.

    Split carefully: a bare IPv6 literal is all colons, so the usual
    ``split(":")[0]`` would reduce ``::1`` to the empty string.
    """
    host = (host or "").strip().lower()
    if host.startswith("["):            # [::1] or [::1]:8069
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:          # host:port - never a bare IPv6 literal
        host = host.partition(":")[0]
    return host in {h.strip("[]") for h in LOOPBACK_HOSTS}


# ------------------------------------------------------------------ helpers
def _clean(url):
    url = (url or "").strip().rstrip("/")
    return url if url.startswith(("http://", "https://")) else ""


def _netloc(url):
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def configured_base_url(env):
    """``web.base.url``, normalised - "" when unset or not a URL."""
    return _clean(env["ir.config_parameter"].sudo().get_param(PARAM_WEB_BASE_URL, ""))


def override_base_url(env):
    """The explicit operator override, normalised - "" when unset."""
    return _clean(env["ir.config_parameter"].sudo().get_param(PARAM_PUBLIC_BASE_URL, ""))


def base_url_is_frozen(env):
    """True when Odoo will stop rewriting ``web.base.url`` on admin login.

    Odoo tests the parameter for truthiness only, so any non-empty value
    freezes it - including the string "False", which is a trap worth knowing
    about but not one to second-guess here.
    """
    return bool(env["ir.config_parameter"].sudo().get_param(PARAM_FREEZE))


# ----------------------------------------------------------------- the answer
def public_base_url(env):
    """The origin AI clients reach us on: ``scheme://host[:port]``, no trailing /.

    Safe to call outside an HTTP request, where it falls back to the configured
    address (crons and tests have no proxy headers to read).
    """
    override = override_base_url(env)
    if override:
        return override

    configured = configured_base_url(env)
    httprequest = _httprequest()
    if httprequest is None:
        return configured

    host = _request_host(httprequest)
    if not host:
        return configured

    # https wins from any signal; nothing here can turn https back into http.
    scheme = "http"
    if _request_scheme(httprequest) == "https":
        scheme = "https"
    if forwarded_scheme(httprequest) == "https":
        scheme = "https"
    if configured.startswith("https://") and _netloc(configured) == host.lower():
        scheme = "https"
    return "%s://%s" % (scheme, host)


def base_url_report(env):
    """Everything the readiness checks need to explain a wrong address.

    Split out from the checks themselves so the diagnosis is testable without
    rendering a screen, and so the one-click fix knows exactly what to write.
    """
    httprequest = _httprequest()
    effective = public_base_url(env)
    configured = configured_base_url(env)
    return {
        "effective": effective,
        "configured": configured,
        "override": override_base_url(env),
        "frozen": base_url_is_frozen(env),
        "proxy_mode": bool(config.get("proxy_mode")),
        # What the WSGI layer believed, before any of our corrections. When
        # this is http on an https deployment, Odoo's ProxyFix is not running,
        # which is also what poisons web.base.url on the next admin login.
        "wsgi_scheme": _request_scheme(httprequest) if httprequest else None,
        "forwarded_scheme": forwarded_scheme(httprequest) if httprequest else None,
        "secure": effective.startswith("https://"),
        "loopback": is_loopback(_netloc(effective)),
        # The configured address disagrees with the one clients actually use.
        # Harmless for MCP now that we correct it, but it is what Odoo mails
        # links with, so it is still worth reporting.
        "configured_differs": bool(configured and effective and
                                   configured != effective),
    }
