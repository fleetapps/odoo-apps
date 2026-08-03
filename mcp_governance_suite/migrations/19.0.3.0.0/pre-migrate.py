# -*- coding: utf-8 -*-
"""Upgrade 19.0.2.0.0 -> 19.0.3.0.0.

Three schema changes need data moved before the ORM touches the tables:

1. ``mcp.oauth.client.dynamically_registered`` (boolean) became
   ``registration_type`` (cimd/dcr/manual).
2. ``mcp.oauth.token.resource`` became **required**. Audience validation also
   changed from fail-open to fail-closed, so a token left without a resource
   would stop authenticating - backfill it with this server's canonical URI,
   which is what those tokens were in fact issued for.
3. ``mcp.oauth.token.client_name`` is new and denormalised at issue time;
   backfill it from the client record so existing rows read correctly.

Creating the columns here means the ORM's later ALTER finds them populated and
its NOT NULL constraint applies cleanly.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # Versions before 19.0.2.0.0 had no OAuth models at all, so neither table
    # exists and there is nothing to move. Bail out rather than let ALTER TABLE
    # abort the upgrade — ADD COLUMN IF NOT EXISTS still fails on a missing
    # relation.
    cr.execute("""
        SELECT to_regclass('mcp_oauth_client'), to_regclass('mcp_oauth_token')
    """)
    client_tbl, token_tbl = cr.fetchone()
    if not client_tbl or not token_tbl:
        _logger.info(
            "MCP: no OAuth tables present (upgrading from %s), nothing to "
            "migrate", version)
        return

    # 1. registration_type ---------------------------------------------------
    cr.execute("""
        ALTER TABLE mcp_oauth_client
        ADD COLUMN IF NOT EXISTS registration_type varchar
    """)
    cr.execute("""
        UPDATE mcp_oauth_client
           SET registration_type = CASE
                   WHEN dynamically_registered THEN 'dcr' ELSE 'manual' END
         WHERE registration_type IS NULL
    """)
    _logger.info("MCP: classified %s OAuth client(s) by registration type",
                 cr.rowcount)

    # 2. token.resource ------------------------------------------------------
    cr.execute("""
        SELECT value FROM ir_config_parameter WHERE key = 'web.base.url'
    """)
    row = cr.fetchone()
    base = (row[0] if row else "").rstrip("/")
    cr.execute("""
        ALTER TABLE mcp_oauth_token
        ADD COLUMN IF NOT EXISTS resource varchar
    """)
    if base:
        cr.execute("""
            UPDATE mcp_oauth_token
               SET resource = %s
             WHERE resource IS NULL OR resource = ''
        """, (base + "/mcp",))
        _logger.info("MCP: bound %s existing token(s) to %s/mcp", cr.rowcount, base)
    else:
        # No base URL to bind to. Revoking is the safe outcome: an unbound
        # token can no longer authenticate anyway, and the user re-authorizes
        # in two clicks.
        cr.execute("""
            UPDATE mcp_oauth_token SET revoked = TRUE, resource = ''
             WHERE resource IS NULL
        """)
        _logger.warning(
            "MCP: web.base.url is unset, so %s OAuth token(s) could not be "
            "bound to an audience and were revoked. Users must reconnect.",
            cr.rowcount)

    # 3. token.client_name ---------------------------------------------------
    cr.execute("""
        ALTER TABLE mcp_oauth_token
        ADD COLUMN IF NOT EXISTS client_name varchar
    """)
    cr.execute("""
        UPDATE mcp_oauth_token t
           SET client_name = c.name
          FROM mcp_oauth_client c
         WHERE c.client_id = t.client_id
           AND t.client_name IS NULL
    """)
