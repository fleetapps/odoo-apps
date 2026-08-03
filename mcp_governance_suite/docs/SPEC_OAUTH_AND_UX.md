# mcp_governance_suite — MCP server, OAuth 2.1, multi-client onboarding

**Version 2** · 2026-08-03 · implementation-ready

Every normative claim below is cited to the official spec or vendor docs. Where
something is *not* verified it says so explicitly. Hand this to a fresh session.

**Target:** `sandbox.odin.ist`, Odoo 19.0-20260723. Deploy path is
`fleetapps/odoo3@19.0`, addons via the `custom_addons` submodule — **not**
`vibe-odoo`. Push order is always odoo-apps first, then odoo3.

---

## 0. Corrections that change the build

### 0.1 There is no "OAuth 2.2"

MCP uses **OAuth 2.1** ([draft-ietf-oauth-v2-1-13](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-13)).
The current MCP revision is **`2026-07-28`** — confirmed by the official docs
URL structure (`modelcontextprotocol.io/docs/2026-07-28/…`). That revision date
is the real event behind the "22/07/2026" claim.

Three independent confirmations that 2.2 does not exist:
- The [MCP authorization spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) cites OAuth 2.1 throughout.
- The competitor listing the claim came from says "OAuth 2.2" in its changelog and **"OAuth 2.0"** in its own setup guide four paragraphs later.
- A *second* competitor module's settings page (user screenshot) is labelled **"Enable OAuth 2.1 Access"**.

### 0.2 Dynamic Client Registration is DEPRECATED

Version 1 of this spec was wrong to treat DCR as the primary fallback. The
normative text:

> Authorization servers and MCP clients **SHOULD** support OAuth Client ID Metadata Documents […]
> Authorization servers and MCP clients **MAY** support […] Dynamic Client Registration […]
> Note that Dynamic Client Registration is **deprecated** and retained for backwards
> compatibility with authorization servers that do not support Client ID Metadata Documents.

**Build CIMD first.** DCR is a compatibility shim, not the happy path. This is
also what makes "two clicks" real: with CIMD the client's `client_id` *is* an
HTTPS URL pointing at its own metadata document, so the user never sees a client
ID or secret. Both Anthropic and OpenAI recommend CIMD.

### 0.3 We are the MCP server

Anthropic/OpenAI SDK client guidance does not apply. We are an **OAuth 2.1
resource server** plus (per §3.1) our own authorization server.

---

## 1. Normative requirements

Source: [MCP Authorization, revision 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).
`MUST`/`SHOULD` below are the spec's own.

| # | Requirement | Level | Status in our code |
|---|---|---|---|
| 1 | MCP server implements **RFC 9728** Protected Resource Metadata | **MUST** | ❌ missing |
| 2 | 401 carries `WWW-Authenticate: Bearer resource_metadata="…"` | **MUST** | ❌ **missing — root cause** |
| 3 | 401 `WWW-Authenticate` includes `scope="…"` | SHOULD | ❌ missing |
| 4 | AS provides **RFC 8414** metadata *or* OIDC Discovery | **MUST** | ❌ missing |
| 5 | AS implements OAuth 2.1 for public **and** confidential clients | **MUST** | ❌ missing |
| 6 | Support **CIMD** (client_id as HTTPS URL) | SHOULD | ❌ missing |
| 7 | DCR (RFC 7591) | MAY, **deprecated** | ❌ — build last, or skip |
| 8 | **RFC 8707** `resource` parameter accepted on authorize + token | **MUST** (client-side; server must honour) | ❌ missing |
| 9 | Validate tokens were issued **for us** as audience | **MUST** | ❌ missing |
| 10 | Only accept our own tokens; **MUST NOT** accept or transit others | **MUST** | ⚠️ partial |
| 11 | Access token in `Authorization: Bearer`, **never** in query string | **MUST** | ✅ correct |
| 12 | Invalid/expired token → **401** | **MUST** | ✅ status right, header wrong |
| 13 | Insufficient scope → **403** + `error="insufficient_scope"` + `scope` + `resource_metadata` | SHOULD | ❌ missing |
| 14 | Malformed authorization request → **400** | **MUST** | ❌ missing |
| 15 | `iss` in authorization responses + `authorization_response_iss_parameter_supported: true` (RFC 9207) | SHOULD | ❌ missing |
| 16 | Do **not** advertise `offline_access` in `WWW-Authenticate` or `scopes_supported` | SHOULD NOT | n/a yet |
| 17 | Transport: **Streamable HTTP** | — | ✅ already correct |

### 1.1 Exact wire formats

**401 — unauthenticated** (spec §Scope Selection Strategy):
```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://sandbox.odin.ist/.well-known/oauth-protected-resource",
                         scope="odoo:read"
```

**403 — token valid but scope too narrow** (spec §Runtime Insufficient Scope Errors):
```http
HTTP/1.1 403 Forbidden
WWW-Authenticate: Bearer error="insufficient_scope",
                         scope="odoo:write",
                         resource_metadata="https://sandbox.odin.ist/.well-known/oauth-protected-resource",
                         error_description="Creating records requires write scope"
```
Emit **all** scopes needed for the operation in one challenge — the spec calls
out incremental challenging as degrading UX.

**Canonical resource URI** (RFC 8707): `https://sandbox.odin.ist/mcp/v1`. No
fragment. Prefer no trailing slash. Tokens must be audience-bound to this exact
value and rejected otherwise.

### 1.2 Current code audit

`controllers/mcp.py` (74 lines) — transport shape is correct: JSON-RPC 2.0 over
POST, `initialize` / `tools/list` / `tools/call` / `ping`.

| Location | Problem | Fix |
|---|---|---|
| `mcp.py:47-48` | `{"error":"unauthorized"}`, 401, **no `WWW-Authenticate`** | §1.1. Claude/ChatGPT have nothing to discover — this alone breaks every OAuth client. |
| `mcp.py:20` | `PROTOCOL_VERSION = "2025-03-26"` | → `"2026-07-28"` |
| `mcp.py:25-35` | Bearer matched only against `mcp.api.key` | Add OAuth token path; keep API keys as documented fallback |
| — | No `.well-known` routes | §3.2 |
| — | No audience validation | Req. 9 |

**Already ahead of the competitor:** they serve `/mcp/sse` (deprecated SSE
transport); we serve Streamable HTTP. Do not regress to SSE for parity.

Existing models to build on: `mcp.api.key`, `mcp.scope`, `mcp.scope.line`,
`mcp.audit.log`, `mcp.approval.request`, `mcp.engine`.

---

## 2. Multi-client support

One OAuth 2.1 implementation serves every MCP client. No per-vendor code.

| Client | Transport | Auth | Verified |
|---|---|---|---|
| **Claude** web/desktop/mobile | Remote HTTPS | OAuth (CIMD/DCR) | ✅ [docs](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) |
| **Claude Code** | Remote or local | OAuth or API key | ✅ |
| **ChatGPT** | Remote HTTPS **only** | OAuth; OpenAI **recommends CIMD**, supports public-client token exchange and signed client assertion. Requires **Developer Mode**, paid plan | ✅ [OpenAI MCP docs](https://developers.openai.com/api/docs/mcp) |
| **VS Code** (Copilot agent mode) | Remote/local | OAuth or key | ✅ [docs](https://code.visualstudio.com/docs/copilot/chat/mcp-servers) |
| **Cursor** | Remote/local | OAuth or key | ✅ [docs](https://cursor.com/docs/context/mcp) |
| M365 Copilot / Copilot Studio, Gemini Enterprise, Perplexity, Mistral Le Chat, Gemini CLI, Codex CLI, Goose, Cline, Windsurf, Zed, Continue.dev, LibreChat | varies | varies | ⚠️ **not individually verified** — plausible via the open standard. Check [modelcontextprotocol.io/clients](https://modelcontextprotocol.io/clients) before making marketing claims. |

**Mobile needs zero work.** Claude mobile picks up connectors from the same
account automatically once OAuth works. State it in docs; don't build for it.

**Marketing guidance:** claim "works with any MCP-compatible client" and link the
official client list, rather than enumerating vendors we haven't tested. The
untested-vendor table above is exactly the kind of claim that generates support
tickets.

### 2.1 Mandatory per-client testing matrix

Ship nothing until these three pass end-to-end. They exercise different
registration paths and will surface different bugs:

1. **Claude Desktop** — CIMD path
2. **ChatGPT Developer Mode** — CIMD + signed assertion path
3. **VS Code or Cursor** — local-client path

---

## 3. Build: authorization

### 3.1 Decision — Odoo is its own authorization server

Self-contained: no external IdP, users already exist in `res.users`, and the
consent screen can state which Odoo user is being authorized. Trade-off: we own
OAuth security correctness. Revisit only if they adopt SSO.

### 3.2 Routes

All `auth="none"`, `csrf=False`, `save_session=False`, **except** `/authorize`
which requires `auth="user"` — it *is* the login + consent step.

```
GET  /.well-known/oauth-protected-resource            RFC 9728
GET  /.well-known/oauth-protected-resource/mcp/v1     path-scoped fallback
GET  /.well-known/oauth-authorization-server          RFC 8414
GET  /mcp/oauth/authorize        auth="user"  → consent → code (+ iss)
POST /mcp/oauth/token                         → code+verifier+resource → tokens
POST /mcp/oauth/revoke                        RFC 7009
POST /mcp/oauth/register                      RFC 7591 — deprecated, build LAST
GET  /mcp/health                              liveness (competitor parity)
```

### 3.3 Metadata payloads

`/.well-known/oauth-protected-resource`:
```json
{
  "resource": "https://sandbox.odin.ist/mcp/v1",
  "authorization_servers": ["https://sandbox.odin.ist"],
  "scopes_supported": ["odoo:read", "odoo:write"],
  "bearer_methods_supported": ["header"]
}
```
`scopes_supported` is the **minimal** set for basic functionality; request more
via step-up. Do **not** list `offline_access` (req. 16).

`/.well-known/oauth-authorization-server`:
```json
{
  "issuer": "https://sandbox.odin.ist",
  "authorization_endpoint": "https://sandbox.odin.ist/mcp/oauth/authorize",
  "token_endpoint": "https://sandbox.odin.ist/mcp/oauth/token",
  "revocation_endpoint": "https://sandbox.odin.ist/mcp/oauth/revoke",
  "registration_endpoint": "https://sandbox.odin.ist/mcp/oauth/register",
  "scopes_supported": ["odoo:read", "odoo:write", "offline_access"],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "private_key_jwt"],
  "authorization_response_iss_parameter_supported": true,
  "client_id_metadata_document_supported": true
}
```
`issuer` **MUST** string-match what we emit as `iss`. `S256` only — never
advertise `plain`.

### 3.4 Models

**`mcp.oauth.client`** — `client_id` (URL for CIMD), `client_name`,
`redirect_uris`, `registration_type` (`cimd`|`dcr`|`manual`),
`client_metadata_json`, `metadata_fetched_at`, `jwks_uri`, `active`.

**`mcp.oauth.grant`** — `code_hash`, `client_id`, `user_id`, `scope_id`,
`code_challenge`, `code_challenge_method`, `redirect_uri`, `resource`,
`expires_at` (≤60s), `used_at`. **Single-use.**

**`mcp.oauth.token`** — `access_token_hash`, `refresh_token_hash`, `user_id`,
`scope_id`, `client_id`, `audience`, `expires_at`, `revoked_at`,
`last_used_at`, `client_name` (denormalised for the UI).

Hash every secret at rest (SHA-256), matching `mcp.api.key`. Never store or log
plaintext tokens. Add an `@api.autovacuum` GC for expired grants/tokens.

### 3.5 Security requirements — non-negotiable

- **PKCE S256 only.** Reject `plain`; reject missing `code_challenge`.
- **Auth codes single-use**; replay **MUST** revoke the token already issued from that code.
- **`redirect_uri` exact string match** against registered value. No prefix matching — that's an open-redirect.
- **`hmac.compare_digest`** for every secret comparison.
- **Audience binding**: token stores `audience`; `/mcp/v1` rejects any token whose audience ≠ our canonical URI (req. 9).
- **`iss` on every authorization response**, including errors (RFC 9207).
- Access tokens ~1h; **rotating** refresh tokens.
- **CIMD fetch hardening**: `client_id` URL must be HTTPS; block private/link-local IPs (SSRF); timeout; cap response size; cache with TTL; re-validate `redirect_uris` on every fetch.
- Scope enforced in **`mcp.engine`**, not only at the endpoint — defence in depth alongside `ir.model.access` and `ir.rule`.
- Rate-limit `/token` and `/authorize`.

### 3.6 Consent screen

The competitor's is genuinely good (user screenshot) — match its clarity:

> **Connect to Odoo**
> Claude wants to access this Odoo instance on your behalf.
> You are signed in as **Administrator**. The assistant will act with your
> account's permissions — it can only see and do what you can.
> [Cancel] [Allow]

Improve on it by also showing: the **client name** (from CIMD metadata, marked
unverified — it is attacker-controllable), the **scopes** being granted in plain
language, and the **expiry**. Never show the raw `client_id` URL as if trusted.

---

## 4. Settings page and permission controls

### 4.1 `res.config.settings` — MCP Server Configuration

Modelled on the competitor's layout (screenshot 6), which is well-organised.
Fields on `res.config.settings`, stored as `ir.config_parameter`:

| Group | Field | Default |
|---|---|---|
| Access | `mcp_enabled` | False |
| Access | → *Manage Available Models* (§4.2) | — |
| Access | → *Manage Custom Tools* | — |
| Auth | `mcp_oauth_enabled` — "Enable OAuth 2.1 Access" | True |
| Auth | → *Manage OAuth Clients* / *Manage OAuth Tokens* | — |
| Auth | `mcp_api_key_enabled` — fallback for local clients | True |
| Auth | `mcp_allowed_origins` (CORS; empty = none) | empty |
| Logging | `mcp_log_requests`, `mcp_log_retention_days` | True, 30 |
| Limits | `mcp_rate_limit_per_min` (0 = unlimited) | 300 |
| Limits | `mcp_default_record_limit` / `mcp_max_record_limit` | 10 / 100 |
| Limits | `mcp_max_smart_fields` | 15 |
| Output | `mcp_max_related_items` | 3 |
| Governance | `mcp_permission_proposals_enabled` (§5.4) | **False** |

Record limits matter more than they look: unbounded reads blow the model's
context and cost. Defaults of 10/100 are sensible.

### 4.2 `mcp.model.access` — the permission matrix

Screenshot 7 shows exactly the right shape. Build this, replacing/extending
`mcp.scope.line`:

Columns: **Model** · **Technical Name** · Active · **Allow Read** · **Allow
Create** · **Allow Update** · **Allow Delete** · **Allow Method Calls** · Last
Modified · Modified By.

- Editable **list** view with `boolean_toggle` widgets — the whole matrix visible and switchable without opening records. This is the single most important screen in the module.
- "Select Multiple Models" bulk-add wizard.
- **Allow Method Calls** is the dangerous one — arbitrary `call_kw`. Off by default, warn in the tooltip, and require an explicit allow-list of method names rather than a blanket boolean.
- Tracked (`Last Modified` / `Modified By`) — it's a security surface.
- **Enforcement is `min(matrix, Odoo ACL)`.** The matrix can only ever *narrow* what the bound user could already do. Never widen. State this in the UI so admins understand it's defence in depth, not a second source of truth.

### 4.3 Per-user access control

- `mcp.api.key` and `mcp.oauth.token` both carry `user_id`; requests run via `request.update_env(user=...)` so `ir.model.access` and `ir.rule` apply. **This is already implemented and is the correct design** — keep it.
- Add `mcp.scope.allowed_group_ids`: only users in these groups may authorize against this scope.
- Add a **kill switch**: revoke all tokens for a user, or globally, in one click.

---

## 5. Tools — making the usage examples work

Each is an `mcp.engine` tool, scope-gated, audit-logged. Naming: `odoo.<verb>_<noun>`.

### 5.1 Read tools

| Example prompt | Tool | Notes |
|---|---|---|
| "Show me all customers from France" | `odoo.search_records` | `model`, `domain`, `fields`, `limit`, `order`. Enforce `mcp_max_record_limit`. |
| "Find products with stock below 10 units" | `odoo.search_records` | needs `product.product` + `qty_available` |
| "List today's sales orders over $1900" | `odoo.search_records` | date/amount domains |
| "Search for unpaid invoices from last month" | `odoo.search_records` | `account.move`, `payment_state` |

One well-designed `search_records` covers four of the seven examples. Supporting
tools: `odoo.list_models`, `odoo.describe_model` (fields + types + selections),
`odoo.read_record`, `odoo.execute_method` (gated by §4.2).

**"Smart fields"** (`mcp_max_smart_fields`): when the caller names no fields,
auto-select the most useful ≤15 rather than dumping every column. Prefer
`_rec_name`, stored non-computed, non-binary fields.

### 5.2 Write tools

| Example prompt | Tool |
|---|---|
| "Create partners from the attached document" | `odoo.create_record` + `odoo.ingest_document` |
| "Create a sale order from the attached customer order" | `odoo.create_record` (+ lines) |

Writes require `odoo:write` scope **and** the matrix bit **and** the user's own
Odoo rights. Default writes to **draft state** wherever the model supports it.

**Document ingestion** overlaps `ai_bill_ocr_community` and `po_so_ai_capture` —
**reuse their extraction, do not reimplement.** Check their `llm_provider`
models first.

### 5.3 Dashboard generation

"Generate a sales dashboard for the current year" → `odoo.create_dashboard`
writing `ir.ui.view` (graph/pivot) + `ir.actions.act_window` + optional
`ir.ui.menu`.

Guardrails: only for models allowed in the matrix; created records **tagged as
AI-generated** and listed on a "Generated Dashboards" screen with bulk delete;
never overwrite an existing view — always create new; require `odoo:write`.

⚠️ Generating `ir.ui.view` arch from a model is an injection surface. Build
arches **programmatically from validated field names**, never by pasting
model-authored XML strings.

### 5.4 Permission proposals — the differentiator

The competitor ships "Claude can control user permissions" as a checkbox. That
is a privilege-escalation path reachable by prompt injection: any untrusted
content the model reads — a PDF, an email, a web page — can carry instructions
to grant access.

We are a **governance** suite. Do it safely, and it becomes the stronger claim:

1. Permission changes are **proposals**, never direct writes → `mcp.approval.request`, human approver required.
2. The model **can never widen its own** scope, key, token, or group. Rejected in `mcp.engine`, not the UI.
3. Full **before → after diff** plus the triggering prompt in `mcp.audit.log`.
4. **Off by default** (`mcp_permission_proposals_enabled`); enabling requires an admin action with a stored written justification.
5. `base.group_system` and this module's own groups are **never** model-editable, at any scope.

Marketing line: *"Claude can propose permission changes. It can never grant
itself access."*

### 5.5 Server instructions ("RAG Trained")

The competitor's "RAG Trained" is marketing for good tool descriptions plus a
server instruction block returned in `initialize`. The substance is real and
cheap: describe Odoo conventions (domains, `search_read`, `_rec_name`,
company scoping, draft-vs-posted) so the model stops guessing. Ship it; don't
adopt the label.

---

## 6. Onboarding — fewer clicks than the competitor

### 6.1 What's wrong with theirs

Documented flow: Settings → enable toggle → *"scroll down and copy the link"* →
Claude → Connectors → paste → Add → Connect → browser → Allow.

1. **"Scroll down and copy the link"** — the page's entire purpose requires scrolling to find.
2. **No feedback loop.** You learn whether it worked by switching to Claude and trying.
3. **No pre-flight.** Non-public `web.base.url` fails inside Claude with an opaque error and no pointer back.
4. Toggle → scroll → copy is three interactions for one job.
5. **No revoke, no visibility** — nothing shows who connected or how to cut them off.

### 6.2 Replacement: `Settings → MCP Governance → Connect your AI`

**One screen. The URL is above the fold. Status is live.**

**Region 1 — Readiness (blocks the URL until green).** Each check states its fix inline:

| Check | Why | Fix shown |
|---|---|---|
| `web.base.url` is public HTTPS | OAuth redirect + Anthropic/OpenAI reachability | link to System Parameters |
| URL resolves publicly | fail here, not inside Claude | — |
| `list_db = False` | a DB selector breaks the OAuth redirect | edit `odoo.conf` |
| ≥1 model in the matrix | a token with no models can do nothing | link to §4.2 |
| ≥1 scope exists | — | link to Scopes |

**Region 2 — The connector URL as hero.** Large, monospace, one-click **Copy**,
**QR code** beside it for mobile. The OAuth switch lives *on this card*.

**Region 3 — Live status.** `Waiting for your first connection…` →
`Connected — 2 users · last call 4 min ago`. Poll `mcp.oauth.token` for a first
successful exchange; flip the card the moment one lands. **This is the single
biggest improvement over the competitor.**

**Region 4 — Connected clients.** One row per active token: user, client name,
scope, connected-at, last-used, **Revoke**. Plus "Revoke all".

**Region 5 — Starter prompts**, as copy-to-clipboard chips, filtered to what the
granted scope can actually satisfy (a read-only scope must not advertise "create
a sale order"). Use the seven examples from §5.

### 6.3 Client picker instead of a linear wizard

A stepped wizard is the wrong shape here — the user is already *in* settings and
most steps happen in a different app. Instead: a **client picker** (Claude ·
ChatGPT · VS Code · Cursor · Other) that swaps the instruction panel to that
client's exact UI path.

- **Claude:** Settings → Connectors → Add custom connector → paste URL → Add → Connect → Allow. Note: leave Client ID/Secret **empty** (CIMD).
- **ChatGPT:** enable **Developer Mode** (paid plan) → Settings → Connectors → create custom connector → paste URL → OAuth.
- **VS Code / Cursor:** show a copy-paste JSON config block.
- **Other:** link [modelcontextprotocol.io/clients](https://modelcontextprotocol.io/clients).

Real click count: **copy URL (1) → paste in client (1) → Allow (1)**. The
readiness checks and live status remove the failure modes that actually cost
time, which matters more than shaving a click.

### 6.4 Troubleshooting panel

Symptom-first, because these are the failures users will hit:

| Symptom | Cause |
|---|---|
| Connector added, **no tools appear** | missing/malformed `WWW-Authenticate` (§1.1), or no models in matrix |
| "Could not connect" | `web.base.url` not public HTTPS, or DB selector on |
| Tools vanish mid-conversation | access token expired; known Claude issue where 401 doesn't trigger re-auth — [claude-ai-mcp#702](https://github.com/anthropics/claude-ai-mcp/issues/702) |
| Works in Claude, not ChatGPT | Developer Mode off, or free plan |
| Read works, write 403s | scope too narrow (§1.1 step-up) or matrix bit off |

### 6.5 Other screens

1. **Model matrix** (§4.2) — highest priority; the security-critical screen.
2. **Approvals** — kanban with the proposed diff inline; approve/reject without opening.
3. **Audit Log** — default to grouped/graph; a flat log is unreadable at volume.
4. **API Keys** — show the secret **once** at creation with a copy button, and say so explicitly.

Empty-state help was added to all four on 2026-08-03 (`odoo-apps` 6e17dea);
these are the structural fixes on top.

---

## 7. Build order

| Phase | Work | Gate |
|---|---|---|
| **1** | 401 `WWW-Authenticate` + both `.well-known` + protocol bump → `2026-07-28` | `curl -i` shows the header; metadata parses |
| **2** | `mcp.oauth.*` models; authorize/token/revoke; PKCE S256; CIMD; `iss`; audience binding | scripted OAuth client gets a working token |
| **3** | Model matrix (§4.2) + settings page (§4.1) | matrix demonstrably narrows access |
| **4** | Connect-your-AI screen (§6.2) + client picker | needs phase 2 tokens for live status |
| **5** | Tools: `search_records`, `describe_model`, `create_record` (§5.1–5.2) | the 7 example prompts work in Claude |
| **6** | Dashboards (§5.3), document ingestion, CRM | each scope-gated + audited |
| **7** | Permission proposals (§5.4) | highest risk — last, behind approvals |
| **8** | DCR (`/register`) — only if a target client lacks CIMD | deprecated; may never be needed |

**Verify each phase against `sandbox.odin.ist` before moving on.** Deploy landed?
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://sandbox.odin.ist/mcp_governance_suite/static/description/icon.png
```
Header correct?
```bash
curl -i -X POST https://sandbox.odin.ist/mcp/v1 -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## 8. Files to change

```
mcp_governance_suite/
├── __manifest__.py                  depends += ['base','web','mail']; add data files
├── controllers/
│   ├── mcp.py                       401 header, protocol bump, OAuth token auth, audience check
│   ├── well_known.py                NEW — RFC 9728 + RFC 8414
│   └── oauth.py                     NEW — authorize/token/revoke/register
├── models/
│   ├── mcp_oauth_client.py          NEW
│   ├── mcp_oauth_grant.py           NEW
│   ├── mcp_oauth_token.py           NEW
│   ├── mcp_model_access.py          NEW — the matrix
│   ├── res_config_settings.py       NEW — §4.1
│   ├── mcp_engine.py                scope + matrix enforcement; new tools
│   └── mcp_scope.py                 add allowed_group_ids
├── views/
│   ├── res_config_settings_views.xml  NEW
│   ├── mcp_model_access_views.xml     NEW — editable matrix list
│   ├── mcp_oauth_views.xml            NEW — clients + tokens + revoke
│   ├── mcp_connect_views.xml          NEW — §6.2 client action
│   └── mcp_views.xml                  scope matrix, approvals kanban, audit grouping
├── static/src/connect/                NEW — OWL: readiness, QR, live status, picker
├── security/ir.model.access.csv       ACLs for every new model
└── docs/SPEC_OAUTH_AND_UX.md          this file
```

QR code: generate server-side with the `qrcode` library (already in the Odoo
image — it's an Odoo dependency). No CDN, no JS library. Odoo Store review
rejects external assets.

---

## 9. Open questions

1. **CIMD in practice** — both vendors *recommend* it, but exact behaviour is unverified against a live connector. Test in phase 2 before promising "no client ID". DCR (phase 8) is the escape hatch.
2. **Scope granularity** — per-model (matrix) only, or also per-record domains? The latter is much stronger for multi-company but materially more work. Recommend shipping per-model first.
3. **Multi-company** — bind tokens to `allowed_company_ids` at authorization, or let the model switch? Binding is safer; switching is more useful. Needs a decision.
4. **Odoo Store review** — OPL-1 modules that ship an OAuth server may attract extra scrutiny. Worth checking guidelines before submission.

---

## 10. Prerequisite — do this first

`odoo3/odoo.conf` commits the live Postgres password, host and user, plus
`admin_passwd = admin`, in a **public** repo; `list_db = True` leaves
`/web/database/manager` publicly answering 200 (verified 2026-08-03).

Rotate before building anything that issues tokens against this database. An
OAuth server on a compromised database is theatre. Note that `list_db = False`
is *also* a functional requirement for the OAuth redirect (§6.2), so this fixes
two problems at once.

---

## 11. References

**Normative**
- [MCP Authorization, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) — the controlling spec
- [OAuth 2.1 draft-13](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-13) · [RFC 9728 PRM](https://datatracker.ietf.org/doc/html/rfc9728) · [RFC 8414 ASM](https://datatracker.ietf.org/doc/html/rfc8414) · [RFC 8707 Resource Indicators](https://www.rfc-editor.org/rfc/rfc8707.html) · [RFC 9207 Issuer ID](https://datatracker.ietf.org/doc/html/rfc9207) · [RFC 6750 Bearer](https://datatracker.ietf.org/doc/html/rfc6750) · [RFC 7591 DCR (deprecated)](https://datatracker.ietf.org/doc/html/rfc7591) · [CIMD draft-00](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-client-id-metadata-document-00)

**Vendor**
- [Claude custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) · [Claude connector OAuth](https://sunpeak.ai/blogs/claude-connector-oauth-authentication/) · [OpenAI MCP](https://developers.openai.com/api/docs/mcp) · [VS Code MCP](https://code.visualstudio.com/docs/copilot/chat/mcp-servers) · [Cursor MCP](https://cursor.com/docs/context/mcp) · [MCP client list](https://modelcontextprotocol.io/clients)

**Odoo 19**
- [Web controllers](https://www.odoo.com/documentation/19.0/developer/reference/backend/http.html) · [Security](https://www.odoo.com/documentation/19.0/developer/reference/backend/security.html) · [Settings/res.config.settings](https://www.odoo.com/documentation/19.0/developer/tutorials/restrict_data_access.html)

⚠️ Odoo 19 renamed `res.groups.category_id` → `privilege_id` (+ new
`res.groups.privilege` model) and `ir.actions.server.groups_id` → `group_ids`.
Both bit this repo on 2026-08-02. Validate new XML with
`scratchpad/schema_check.py` before deploying.
