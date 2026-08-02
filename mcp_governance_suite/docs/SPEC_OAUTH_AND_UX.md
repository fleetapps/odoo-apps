# mcp_governance_suite — OAuth 2.1 + onboarding UX spec

Written 2026-08-03. Hand this to a fresh session; everything below is verified,
so none of the research needs re-deriving.

Target: `sandbox.odin.ist` (Odoo 19.0-20260723). Deploy path is
`fleetapps/odoo3@19.0` with addons via the `custom_addons` submodule — see
`odoo-sandbox-deploy-topology` in memory, not `vibe-odoo`.

---

## 0. Two corrections that change the build

**There is no OAuth 2.2.** MCP and Claude custom connectors use **OAuth 2.1**.
The confusion is traceable: the competitor listing this spec draws from
(`rag_odoo_mcp_server` on apps.odoo.com) says "OAuth 2.2" in its changelog and
"OAuth 2.0" in its own setup guide four paragraphs later. Both are wrong. What
actually shipped is the **MCP authorization spec revision `2026-07-28`** — which
is the real event behind the "22/07/2026" date.

**We are the MCP _server_, not a Claude API client.** Anthropic SDK guidance
(the `claude-api` skill) is the wrong side of the wire and does not apply. What
governs this build is the MCP server-side authorization contract below.

---

## 1. Verified server-side contract

Sources: [Claude connector OAuth](https://sunpeak.ai/blogs/claude-connector-oauth-authentication/),
[custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp),
[MCP connector docs](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector).

Claude is the OAuth client, Odoo is the protected resource, and the
authorization server is ours (built in Odoo — see §3).

| # | Requirement | Where |
|---|---|---|
| 1 | Public HTTPS, reachable from Anthropic IP ranges | infra |
| 2 | **Streamable HTTP** transport (SSE is deprecated) | `/mcp/v1` — already correct |
| 3 | Protected Resource Metadata at `/.well-known/oauth-protected-resource`, plus the `/.well-known/oauth-protected-resource/<mcp-path>` fallback | new |
| 4 | 401 carries `WWW-Authenticate: Bearer resource_metadata="…", scope="…"` | **fix required** |
| 5 | Authorization Server Metadata at `/.well-known/oauth-authorization-server` | new |
| 6 | Authorization Code + **PKCE (S256)** | new |
| 7 | **CIMD** preferred, **DCR** (`/register`) as fallback | new |

**Item 4 is the single reason the current module cannot work as a modern
connector.** Claude discovers the whole flow from that header; without it there
is nothing to discover and the connector silently fails.

### Current state (audited 2026-08-03)

`controllers/mcp.py` is 74 lines and structurally sound — JSON-RPC 2.0 over POST
with `initialize` / `tools/list` / `tools/call` / `ping`. Three gaps:

- **`mcp.py:47-48`** — 401 returns `{"error": "unauthorized"}` with **no
  `WWW-Authenticate` header**.
- **`mcp.py:20`** — `PROTOCOL_VERSION = "2025-03-26"`, carrying its own
  `VERIFY-ON-BUILD` note. Current revision is `2026-07-28`.
- No `.well-known` endpoints, no authorization server. Auth today is a
  hand-pasted bearer key SHA-256'd against `mcp.api.key`.

One thing already **ahead** of the competitor: they serve `/mcp/sse`, the
deprecated SSE transport. We already use Streamable HTTP. Don't regress to SSE
for parity.

Existing models to build on: `mcp.api.key`, `mcp.scope`, `mcp.scope.line`,
`mcp.audit.log`, `mcp.approval.request`, `mcp.engine`.

---

## 2. Competitor feature map — what to copy, adapt, or refuse

Claims taken from the `rag_odoo_mcp_server` listing, assessed rather than
mirrored.

| Their claim | Verdict | Notes |
|---|---|---|
| Claude **mobile app** support | **Free** — no work | Follows automatically from being a proper OAuth remote connector. Do not build anything; state it in docs. |
| "OAuth 2.2, two clicks, no API keys" | **Build as OAuth 2.1** | §3. The two-click experience is real and comes from CIMD — user pastes a URL, never handles a client ID/secret. |
| **Per-user native Odoo API key** | **Mostly done** | `mcp.api.key` + `request.update_env(user=...)` already runs as the key's user, so `ir.model.access` and `ir.rule` apply. Keep as the non-OAuth fallback; surface key management in the UI. |
| "RAG Trained" | **Achievable, rename** | Marketing term for shipping good tool descriptions + a server instruction block. Substance is real; the label is not. |
| **Dashboard generation** | **Achievable** | A `create_dashboard` tool writing `ir.ui.view` + `ir.actions.act_window`. Must be scope-gated and audit-logged. |
| **Document detection** (PDF → sale order/partner) | **Achievable** | Overlaps `ai_bill_ocr_community` and `po_so_ai_capture` — reuse, don't reimplement. |
| **CRM manager** (lead gen, promo emails) | **Achievable, gate hard** | Anything that *sends* email must be approval-gated. Drafts only by default. |
| **Multi-company detection** | **Achievable** | Respect `company_ids` on the bound user; never cross-company without explicit scope. |
| **"Claude Can Now Control User Permissions"** | **Build — but not as a toggle** | See below. |

### On letting Claude manage permissions

Their version is a config checkbox that lets the model rewrite access-control
rules. That is a privilege-escalation path reachable by prompt injection: any
untrusted content the model reads — a PDF, an email body, a web page — can carry
instructions to grant itself rights.

We are a **governance** suite. The differentiator is doing this safely, and the
safe version is strictly better product, not a watered-down one:

1. Permission changes are **proposals**, never direct writes — they land in
   `mcp.approval.request` and require a human approver.
2. The model **can never widen its own** scope, key, or group. Self-referential
   changes are rejected at the engine, not the UI.
3. Every proposal records the **full diff** (before → after) plus the prompt
   that triggered it, in `mcp.audit.log`.
4. Disabled by default; enabling requires an explicit admin action with a
   written justification stored on the record.
5. `base.group_system` and the module's own groups are **never** editable by the
   model, at any scope.

Ship this as the headline: *"Claude can propose permission changes. It can never
grant itself access."* That is a feature the competitor cannot claim.

---

## 3. Auth build

### 3.1 New endpoints

```
GET  /.well-known/oauth-protected-resource          → RFC 9728 metadata
GET  /.well-known/oauth-protected-resource/mcp/v1   → same (path-scoped fallback)
GET  /.well-known/oauth-authorization-server        → RFC 8414 metadata
POST /mcp/oauth/register                            → RFC 7591 DCR
GET  /mcp/oauth/authorize                           → consent screen → code
POST /mcp/oauth/token                               → code+PKCE → access/refresh
POST /mcp/oauth/revoke                              → RFC 7009
```

All `auth="none"`, `csrf=False`, `save_session=False`, except `/authorize`
which needs a live Odoo session (it *is* the login+consent step).

### 3.2 Models

- `mcp.oauth.client` — `client_id`, `client_name`, `redirect_uris`,
  `registration_type` (`cimd` | `dcr` | `manual`), `created_via`.
- `mcp.oauth.grant` — short-lived auth code: `code_hash`, `client_id`,
  `user_id`, `scope_id`, `code_challenge`, `code_challenge_method`,
  `redirect_uri`, `expires_at` (≤60s), `used` flag. **Single-use.**
- `mcp.oauth.token` — `access_token_hash`, `refresh_token_hash`, `user_id`,
  `scope_id`, `client_id`, `expires_at`, `revoked_at`, `last_used_at`.

Hash every secret at rest (SHA-256), matching `mcp.api.key`'s existing pattern.
Never store or log a plaintext token.

### 3.3 Hard requirements

- PKCE **S256 only** — reject `plain`, reject a missing `code_challenge`.
- Auth codes single-use; replay revokes the issued token (RFC 6749 §4.1.2).
- `redirect_uri` matched **exactly** against the registered value.
- Constant-time comparison on every secret (`hmac.compare_digest`).
- Access tokens short-lived (~1h) with rotating refresh tokens.
- Scope is bound to a `mcp.scope` record and enforced in `mcp.engine`, not just
  at the endpoint.
- Bump `PROTOCOL_VERSION` to `2026-07-28`.
- Keep the API-key path working — it's the documented fallback.

### 3.4 The 401 fix

```python
return request.make_json_response({"error": "unauthorized"}, status=401, headers=[
    ("WWW-Authenticate",
     f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'),
])
```

Do this first and in isolation — it's the smallest change that moves the
connector from "silently broken" to "discoverable".

---

## 4. Onboarding UX

### 4.1 What's wrong with the competitor's flow

Their documented steps: Settings → enable toggle → *"scroll down and copy the
link"* → Claude → Connectors → paste → Add → Connect → browser → Allow.

Five real problems:

1. **"Scroll down and copy the link"** — if the thing the whole page exists for
   requires scrolling to find, the page is laid out wrong.
2. **No feedback loop.** Nothing tells you whether it worked. You find out by
   switching to Claude and trying.
3. **No pre-flight.** If `web.base.url` isn't public HTTPS, the connector fails
   inside Claude with an opaque error and no pointer back to the cause.
4. **Toggle → scroll → copy** is three interactions for one job.
5. **No revoke, no visibility.** Nothing shows who has connected or lets you cut
   a connection off.

### 4.2 Replacement: one screen, `Settings → MCP Governance → Connect to Claude`

**Region 1 — Readiness.** Runs before anything else and blocks the URL until it
passes. Each check states the fix inline:

- `web.base.url` is public HTTPS (not `localhost`, not `http://`)
- The URL resolves publicly (fail early rather than in Claude)
- `list_db = False` — a DB selector breaks the OAuth redirect
- At least one `mcp.scope` exists (a key with no scope can do nothing)

**Region 2 — The URL, as the hero.** Not below the fold, not behind a toggle.
Large, monospace, one-click **Copy**, with a QR code beside it for the mobile
app. Enabling OAuth is a switch *on this card*, not a separate page.

**Region 3 — Live status.** Replaces "hope it worked":
> `Waiting for Claude…` → `Connected — 2 users, last call 4 minutes ago`

Poll `mcp.oauth.token` for a first successful exchange. The moment one lands,
the card flips state. This is the single biggest improvement over their flow.

**Region 4 — Connected users.** One row per active token: user, client name,
scope, connected-at, last-used, **Revoke**. Answers "who has access?" and "how
do I cut it off?" — neither of which their UI addresses at all.

**Region 5 — Starter prompts.** Their usage examples live in a web listing; put
them *in the product*, as copy-to-clipboard chips:

- "Generate a sales dashboard for this year"
- "Find products with stock below 10 units"
- "List unpaid invoices from last month"
- "Create a sale order from the attached document"

Only show prompts the granted scope can actually satisfy — a read-only scope
shouldn't advertise "create a sale order".

### 4.3 Instructions panel

A numbered, collapsible **"Add this to Claude"** with the real UI path
(Settings → Connectors → Add custom connector → paste → Add → Connect →
Allow), and a note that the same account on Claude mobile picks the connector up
automatically. Include the 401/`WWW-Authenticate` symptom in a troubleshooting
line — "connector added but no tools appear" is the failure users will hit.

### 4.4 Fixing the rest of the module

The four existing screens (API Keys, Scopes, Approvals, Audit Log) got
empty-state help on 2026-08-03 (`odoo-apps` 6e17dea) but are otherwise bare
list/form views. Priority order:

1. **Scopes** — a permission matrix (models × read/write/create/delete), not a
   line-item list. This is the security-critical screen and currently the least
   legible.
2. **Approvals** — kanban with the proposed diff rendered inline; approve/reject
   without opening the record.
3. **Audit Log** — default to a grouped/graph view; a flat log of every call is
   unreadable at volume.
4. **API Keys** — show the secret exactly once at creation with a copy button,
   and never again. Make that explicit in the UI.

---

## 5. Build order

| Phase | Work | Why first |
|---|---|---|
| 1 | 401 `WWW-Authenticate` + both `.well-known` endpoints + protocol bump | Smallest change from "broken" to "discoverable". Independently testable with `curl`. |
| 2 | `mcp.oauth.*` models + authorize/token/register/revoke with PKCE | The actual auth. Test with a scripted client before involving Claude. |
| 3 | Connect-to-Claude screen (§4.2) | Needs phase 2's token records for live status. |
| 4 | Scope matrix, approvals kanban, audit views (§4.4) | Independent of auth; can run in parallel. |
| 5 | Tools: dashboards, documents, CRM, permission proposals (§2) | Each scope-gated and audit-logged. Permission proposals last — highest risk. |

**Verify each phase against `sandbox.odin.ist` before moving on.** The static
file trick confirms deploys landed:
`curl -o /dev/null -w "%{http_code}" https://sandbox.odin.ist/mcp_governance_suite/static/description/icon.png`

---

## 6. Open questions

1. **Is Odoo the authorization server, or do we federate?** This spec assumes
   Odoo issues its own tokens (self-contained, no external IdP). If they use
   Google/Azure SSO, federating is better but bigger.
2. **CIMD support** — worth confirming Claude's exact CIMD behavior against a
   live connector before relying on it for the two-click claim. DCR is the safe
   fallback and should be built regardless.
3. **Scope granularity** — per-model (current `mcp.scope.line`) or per-model
   *plus* per-record domain? The latter is much stronger for multi-company but
   materially more work.

## 7. Prerequisite, unrelated to this work

`odoo3/odoo.conf` commits the live Postgres password, host, and user, plus
`admin_passwd = admin`, in a **public** repo, and `list_db = True` leaves
`/web/database/manager` publicly answering 200. Rotate before building anything
that issues tokens against this database — an OAuth server on top of a
compromised DB is theatre.
