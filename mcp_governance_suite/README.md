# AI MCP

**Connect Claude, ChatGPT, Gemini, Cursor, Copilot or any MCP client to Odoo —
with one-click OAuth 2.1 — and let your team chat with their data, build reports
and draft records. Every AI action runs as the *real* Odoo user, scoped,
rate-limited, audited, and (optionally) approval-gated.**

The Model Context Protocol (MCP) server runs **inside Odoo**. There is no extra
process, no middleware, and **zero external Python dependencies**.

- **Odoo:** 19.0 (and 18.0)
- **License:** OPL-1
- **Depends:** `base`, `web`, `mail`

---

## Table of contents
1. [Why this module](#why-this-module)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Quick start — connect an assistant](#quick-start--connect-an-assistant)
5. [User guide](#user-guide)
6. [Administrator guide](#administrator-guide)
7. [Security model](#security-model)
8. [Developer guide](#developer-guide)
9. [Extension guide](#extension-guide)
10. [FAQ](#faq)
11. [Troubleshooting](#troubleshooting)
12. [Roadmap](#roadmap)
13. [Changelog](#changelog)

---

## Why this module

A bare MCP connector hands an AI a shared admin token and hopes for the best.
That never clears an enterprise security review. This module is the **governance
layer** that makes AI-over-ERP approvable:

| Concern | How it's answered |
|---|---|
| *"Whose permissions does the AI have?"* | The signed-in user's. Every call runs through the ORM as them — `ir.model.access`, record rules, field groups and multi-company all apply. |
| *"What can it touch?"* | A **scope**: per-model read/create/write/delete, field blacklists, extra record domains, row caps. Read-only by default. |
| *"Can it change things without me?"* | Only if you allow it. Turn on **approval** and every write becomes a human-reviewed request. |
| *"What did it do?"* | The **audit log**: one attributable row per call (user, tool, model, IP, duration, token estimate). |
| *"How do users connect?"* | **OAuth 2.1** — paste one URL, click *Allow*. No keys, no config files. |

---

## Installation

1. Copy `mcp_governance_suite/` into your Odoo addons path.
2. Update the apps list and install **AI MCP**
   (Apps → search "AI MCP").
3. Confirm the base URL is correct: **Settings → Technical → System Parameters →
   `web.base.url`** must be the externally reachable HTTPS URL. OAuth redirect
   URIs and token audiences are derived from it. Behind a reverse proxy, also set
   `--proxy-mode` (or `proxy_mode = True`).

> **HTTPS is required** for OAuth. Redirect URIs must be `https://` or loopback
> (`http://localhost`, `http://127.0.0.1`).

After install the endpoints are live:

| Endpoint | Purpose |
|---|---|
| `POST /mcp` | MCP Streamable HTTP endpoint (JSON-RPC 2.0) |
| `GET  /mcp/health` | Liveness + capabilities |
| `GET  /.well-known/oauth-protected-resource` | RFC 9728 resource metadata |
| `GET  /.well-known/oauth-authorization-server` | RFC 8414 server metadata |
| `GET  /oauth/authorize` | Consent screen (Odoo login) |
| `POST /oauth/token` | PKCE token + refresh |
| `POST /oauth/revoke` | RFC 7009 token revocation |
| `POST /oauth/register` | RFC 7591 dynamic registration (deprecated fallback) |

Clients register through **Client ID Metadata Documents** — the client's
`client_id` is an HTTPS URL serving its own metadata, so nobody ever copies a
client ID or secret. Dynamic registration is kept only for clients that cannot
do CIMD; the MCP specification deprecated it.

---

## Configuration

**Settings → AI MCP.**

| Setting | Default | Notes |
|---|---|---|
| Enable MCP Server | On | Master switch for `/mcp`. |
| Sign in with Odoo (OAuth 2.1) | On | The recommended connect flow. |
| Allow API-key connections | On | Headless/CI path. |
| Dynamic client registration | On | RFC 7591, deprecated upstream. CIMD needs no setting. |
| Allowed browser origins | *(empty)* | Empty is safe: MCP clients aren't browsers. |
| Default OAuth scope | *Read-only (safe default)* | Applied to users without a personal scope. |
| Access token lifetime | 3600 s | Short-lived is safer. |
| Refresh token lifetime | 2 592 000 s (30 d) | Rotated on every refresh. |
| Audit retention | 12 months | Purged weekly by cron. |

The **Connect** block links to the Connect screen described below.

---

## Quick start — connect an assistant

Open **AI MCP → Connect your AI**. Everything you need is on that one page:

1. **Before you start** — every precondition checked, in plain language, each
   with a button that opens the exact screen where it is fixed. You find out
   here rather than inside Claude with an error that explains nothing.
2. **Your server URL** — one click to copy, plus a QR code for mobile.
3. **Live status** — leave the page open. It flips from *Waiting for your first
   connection…* to *Connected* the moment an assistant signs in.
4. **Add it to your assistant** — pick Claude, ChatGPT, VS Code, Cursor or
   Other and the steps change to that client's exact UI path. Local clients get
   a copy-paste JSON config block.
5. **Try one of these** — starter prompts as copy-to-clipboard chips, filtered
   to what your permissions can actually satisfy.
6. **Connected assistants** — who is connected, on what, last used, with
   *Disconnect* per row.

Real click count: copy the URL, paste it in your assistant, click Allow.

### API key (headless)
AI MCP → **API Keys** → New → *Generate Key* (copy it once).
Send it as `Authorization: Bearer <key>`.

### Try it
> "List my capabilities."
> "Show me customers from France."
> "Revenue by month this year."
> "How many partners have no email?"

---

## User guide

- **Connect your AI** — readiness checks, the URL, live status and per-client steps.
- **My AI Activity** — every action an assistant has taken on your behalf.
- **Connections** — your live OAuth sessions. Revoke any to disconnect instantly.
- **API Keys** — your keys; rotate or deactivate at will.

The AI cannot exceed your own Odoo permissions. If you are read-only, it is
read-only. If you cannot delete, neither can it.

---

## Administrator guide

### Model Permissions — the switchboard

**AI MCP → Permissions → Model Permissions** is the single most important
screen: one row per model, one column per operation, every toggle switchable
directly in the list. Use **Add Models** to bring in a whole set at once with
one preset.

| Column | Meaning |
|---|---|
| Read / Create / Update / Delete | The four data operations |
| **Method Calls** | Business actions (confirm, post, send). Off by default |
| Allowed Methods | The exact method names permitted — empty allows nothing |
| Field blacklist | Fields never returned or accepted, e.g. `vat,bank_ids` |
| Record domain | Extra domain ANDed into every query on that model |

> **It can only ever take access away, never add it.** Every call runs as the
> signed-in Odoo user, so the effective permission is whichever is narrower:
> this matrix, or that user's own rights. Enabling something here does not
> grant it — it stops this layer blocking it.

**Method calls** deserve care. An assistant that can invoke `action_post` or a
mailing method will eventually do so at an awkward moment, so the switch alone
grants nothing: you must also name the exact methods. Private methods
(leading underscore) and raw ORM verbs (`write`, `unlink`, `sudo`, `search`…)
are refused at save time and again at call time.

**Scopes** (AI MCP → Permissions → Scopes) wrap the matrix:

- **Read Only** — global kill-switch; write tools are not even advertised.
- **Require Approval** — writes and method calls become approval requests.
- **Rate limit / Max records** — throughput and row caps.
- **Capabilities** — which tool bundles are exposed (empty = all).

The shipped **Read-only (safe default)** scope grants read on `res.partner`,
`res.company`, `res.country`, `res.currency`. Extend it (or clone it) to reach
`sale.order`, `crm.lead`, `account.move`, `stock.picking`, etc.

Assign scopes per user in **Settings → Users → (user) → MCP Scope**, or set the
database-wide default in **Settings → AI MCP**.

**Approvals** (AI MCP → Approvals) — a kanban of AI-requested writes. Approve
to execute *as the requesting user*; reject to discard. Approvers are notified
via chatter/activities.

**Audit Log** — list/graph/pivot of every call, with token totals for cost
visibility.

---

## Security model

```
AI client ──Bearer(OAuth token | API key)──▶ /mcp
      │
      ▼  authenticate → switch env to the user
  MCP governance scope   (capability gate, per-model ops, blacklist, domain, caps)
      │
      ▼  execute through the ORM AS THE USER
  ir.model.access · ir.rule · field groups · multi-company   ← native Odoo security
      │
      ▼
  audit log  (+ approval gate for writes)
```

- **Identity:** OAuth 2.1 with PKCE (S256); users log in with their own Odoo
  credentials — SSO/2FA included. No shared admin token.
- **Audience binding (RFC 8707):** tokens are minted for *this* MCP endpoint and
  refused elsewhere.
- **Tokens at rest:** only SHA-256 digests are stored. Plaintext is shown once.
- **Refresh rotation:** every refresh revokes the previous refresh token.
- **Defence in depth:** the scope can only ever *narrow* what the user can do,
  never widen it.

---

## Developer guide

**Tools** are records (`mcp.tool`), each binding a name + JSON schema to a
generic engine *handler*:

| Handler | Tool | Notes |
|---|---|---|
| `list_capabilities` / `list_models` / `get_schema` / `get_business_context` | discovery | teach the AI |
| `search_records` / `count_records` / `name_search` | read | domain-driven |
| `read_group` | aggregate | powers reports & dashboards |
| `create_record` / `write_record` / `unlink_record` | write | governed, approval-aware |
| `call_method` | action | allow-listed business methods only; approval-aware |

All data access uses the acting user's env; only governance *config* is read with
elevated rights. Domains are parsed with `ast.literal_eval` (never `eval`).

**Transport:** MCP Streamable HTTP. `POST /mcp` returns a single JSON-RPC
response.

The server is **dual-era**, so it works with both generations of MCP client:

| Era | Revisions | How a request opens |
|---|---|---|
| Modern | `2026-07-28` | Per-request `_meta` + `MCP-Protocol-Version` header; no handshake, no session. `server/discover` reports what we support. |
| Legacy | `2025-06-18`, `2025-03-26` | `initialize` handshake, negotiated down. |

`2026-07-28` removed the `initialize` handshake, the GET stream and
protocol-level sessions. Answering only one era breaks the other outright, so
both are implemented: an `initialize` call selects legacy semantics, modern
`_meta` selects modern.

---

## Extension guide

Add capabilities **without touching Python** — ship data:

```xml
<record id="tool_my_verb" model="mcp.tool">
    <field name="name">my_verb</field>
    <field name="capability_id" ref="cap_data_read"/>
    <field name="handler">search_records</field>
    <field name="description">What the AI should know about this tool.</field>
    <field name="input_schema">{"type":"object","properties":{"model":{"type":"string"}},"required":["model"]}</field>
</record>
```

Need a genuinely new verb? Inherit the engine and add a handler:

```python
class MCPEngine(models.AbstractModel):
    _inherit = "mcp.engine"

    def _handler_my_custom(self, scope, args):
        line = self._require_line(scope, args["model"], "read")
        ...  # self.env runs as the user; enforce the scope, then return a dict
```

Then point an `mcp.tool` row's `handler` at `my_custom`. Add `mcp.context`
records to teach the AI your domain, and `mcp.prompt` records to ship a vertical
prompt pack.

---

## FAQ

**Which AI clients work?** Any MCP client that speaks Streamable HTTP — Claude,
ChatGPT, Gemini, Cursor, Copilot, VS Code, and more.

**Do I need `mcp-remote` or a proxy?** No. The server is native to Odoo.

**Does the AI get admin rights?** Never. It operates as the connected user,
bounded further by the scope.

**Can it change data?** Only if the scope allows writes — and even then you can
require human approval for every one.

**Is my data sent to train a model?** This module only exposes an endpoint; what
your AI provider does with prompts is governed by *your* agreement with them.
Choose a read-only scope and blacklist sensitive fields to be conservative.

**Multi-company?** Yes. Sessions see the user's allowed companies; record rules
prevent cross-company leakage.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Client won't start OAuth | `web.base.url` must be the external HTTPS URL; check `GET /.well-known/oauth-protected-resource` returns JSON. |
| `401` on `/mcp` | Expected without a token — the `WWW-Authenticate` header points the client to discovery. Ensure the client completed OAuth. |
| Redirect rejected | Redirect URIs must be `https://` or loopback and exactly match the registered value. |
| Tool "not available in this scope" | The tool's capability isn't enabled on the scope, or the scope is read-only and the tool writes. |
| "Scope denies read on X" | Add a Model Access line for model X to the scope. |
| Writes never happen | The scope requires approval — check AI MCP → Approvals. |
| Everything 503 | The master switch (Settings → AI MCP → Enable MCP Server) is off. |

---

## Roadmap

- Legacy HTTP+SSE transport bridge for older clients.
- Document Intelligence pack (drag a PDF/invoice → drafted records).
- Workflow automation ("every Friday, email me a summary").
- Saved dashboards materialised inside Odoo.
- Per-capability OAuth scopes (down-scope at consent time).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
