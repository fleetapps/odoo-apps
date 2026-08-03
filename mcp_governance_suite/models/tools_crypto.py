# -*- coding: utf-8 -*-
"""Small cryptographic helpers shared by the API-key and OAuth 2.1 layers.

Secrets are never stored at rest: only their SHA-256 digest is persisted, so a
database dump can never be replayed against the MCP endpoint. The plaintext is
surfaced to a human exactly once (reveal wizard / OAuth redirect).

PKCE reference (RFC 7636 / OAuth 2.1 §7.5.2):
https://datatracker.ietf.org/doc/html/rfc7636
"""
import base64
import hashlib
import secrets


def new_secret(prefix="", nbytes=32):
    """Return a URL-safe random secret, optionally prefixed for readability."""
    raw = secrets.token_urlsafe(nbytes)
    return f"{prefix}{raw}" if prefix else raw


def hash_secret(value):
    """One-way digest used for every secret we persist (keys, codes, tokens)."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def pkce_s256_challenge(verifier):
    """Derive the S256 code_challenge from a PKCE code_verifier.

    challenge = BASE64URL-ENCODE(SHA256(ASCII(verifier))) without padding.
    """
    digest = hashlib.sha256((verifier or "").encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def constant_time_equals(left, right):
    """Timing-attack-resistant comparison for secret material."""
    return secrets.compare_digest(left or "", right or "")
