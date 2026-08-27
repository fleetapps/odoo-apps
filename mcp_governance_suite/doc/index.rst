======
AI MCP
======

Connect Claude, ChatGPT, Gemini, Cursor, Copilot or any Model Context Protocol
client to Odoo with one-click OAuth 2.1. Every AI action runs as the signed-in
Odoo user — scoped, rate-limited, audited and optionally approval-gated. The
MCP server runs inside Odoo: no extra process, no middleware, and no external
Python dependencies.

Live Demo
=========

A live instance is available for testing without installing anything:
https://sandbox.odin.ist/odoo/settings#mcp_governance_suite — log in with
username ``admin`` and password ``admin``.

Installation
============

Download the module and add it to your Odoo addons folder. Log on to your
Odoo server, go to the Apps menu, enable developer mode and click
"Update Apps List". Search for "AI MCP" and click Install.

After install, confirm **Settings → Technical → System Parameters →
``web.base.url``** is your externally reachable HTTPS URL — OAuth redirect
URIs and token audiences are derived from it. HTTPS is required; redirect
URIs must be ``https://`` or loopback (``http://localhost``).

Access data for this OPL-1 module is provided at purchase. If you did not
purchase directly and need access, contact support (support@odin.ist) with a
confirmation of purchase.

Upgrade
=======

Download the new version, replace the module in your addons folder, restart
the server, then upgrade the module from the Apps menu. No manual data
migration is required between minor versions.

Configuration
=============

Go to **Settings → AI MCP** to enable the server and choose a connect
method (OAuth 2.1 is the default and recommended path; API keys remain
available for headless/CI use).

Then, under **AI MCP → Permissions**:

* **Model Permissions** — one row per model, one column per operation
  (read/create/update/delete), plus an allow-list of callable methods and an
  optional field blacklist and record domain per model.
* **Scopes** — bundle a set of model permissions with a Read Only switch, an
  approval requirement, a rate limit and a row cap. Assign a scope per user
  under **Settings → Users**, or set the database-wide default here.

Every call still runs through Odoo's own ``ir.model.access`` and record rules
as the connected user — a scope can only narrow what a user can already do,
never widen it.

Users connect from **AI MCP → Connect your AI**: copy the server URL,
paste it into their MCP client, sign in with their normal Odoo credentials
and click Allow.

Usage
=====

Once connected, an assistant can discover what it's allowed to do and act
within that scope — no further setup needed for day-to-day use.

* **My AI Activity** — every action an assistant has taken on the connected
  user's behalf.
* **Connections** — live OAuth sessions; revoke any to disconnect instantly.
* **Approvals** — a kanban of AI-requested writes waiting on human sign-off,
  for scopes that require it.
* **Audit Log** — one attributable row per call: user, tool, model, IP,
  duration and a token estimate for cost tracking.

Credits
=======

Author & Maintainer
--------------------

This module is maintained by `Fleet <https://fleet.ke>`_.

If you want to get in touch, contact support (developers@fleet.ke,
support@odin.ist, andrew@fleet.ke) or visit our website (https://fleet.ke).
