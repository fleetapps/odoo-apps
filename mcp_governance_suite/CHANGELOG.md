# Changelog

All notable changes to **Fleet AI — MCP Governance Suite** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/); versions use
Odoo's `19.0.MAJOR.MINOR.PATCH` scheme.

## [19.0.3.1.0] — 2026-08-03

### Added
- **Model Permissions screen** — the access matrix is now a top-level,
  editable list: one row per model, one column per operation, toggles
  switchable without opening a record. Grouped by scope, with filters for
  "can change data", "can delete" and "can call methods", plus Last Modified /
  Modified By columns.
- **Add Models picker** — multi-select any number of models, apply one preset
  (read / read+create+update / full), skip anything already present. Shows what
  it will do before you commit.
- **Method-call allow-list** — a new `call_method` tool lets an assistant run
  business actions (confirm, post, send) on records, gated four times over: the
  matrix bit, a per-model allow-list of exact method names, a global denylist of
  ORM and privilege verbs, and the acting user's own Odoo rights. The bit alone
  grants nothing — an empty allow-list permits nothing. Approval-gated like any
  other write, and re-validated at approval time in case the matrix changed.
- **"Connect your AI" screen** — replaces the old wizard. Readiness checks with
  inline fixes, the server URL as a one-click copy plus QR code, live status
  that flips to *Connected* the moment a token lands, a client picker that
  swaps in each client's exact steps, starter-prompt chips filtered to what the
  current permissions allow, and connected assistants with per-row Disconnect.
- **"My AI Activity" menu** — users can now see their own audit trail. The
  record rule always permitted this; there was simply no way to reach it.

### Changed
- Permission errors now name the model, the operation and where to fix it,
  instead of a bare "access denied".
- Scope lines can be archived to suspend access without losing configuration.
- Menus reorganised: Permissions is now its own top-level section rather than
  being buried under Configuration.

### Removed
- The **Connect an AI Assistant** wizard, superseded by the Connect screen. API
  keys are still generated from the API Keys form.

### Fixed
- **Module would not install on Odoo 19.** The Settings action in
  `views/res_config_settings_views.xml` set `target="inline"`, which was dropped
  from the `ir.actions.act_window.target` selection in 19 (only `current`,
  `new`, `fullscreen` and `main` remain), aborting the install with
  `ValueError: Wrong value for ir.actions.act_window.target: 'inline'`. The
  field is now omitted, matching core `base_setup`; the settings page renders
  identically on the `current` default.

## [19.0.3.0.0] — 2026-08-03

### Added
- **Protocol revision `2026-07-28`.** The server is now **dual-era**: it serves
  both the modern per-request-metadata shape (no `initialize` handshake, no
  protocol-level sessions, mandatory `server/discover`) and the older
  handshake-based revisions. Adds `UnsupportedProtocolVersionError` (`-32022`),
  `HeaderMismatch` (`-32020`) header/body validation, and `404` for unknown
  methods on modern requests.
- **Client ID Metadata Documents (CIMD)** — clients identify themselves with an
  HTTPS URL serving their own metadata, so no client ID or secret is ever
  copied by a human. Advertised via `client_id_metadata_document_supported`.
  Fetching is SSRF-hardened (public addresses only, no redirects, size cap,
  timeout) and cached with a TTL; `redirect_uris` are re-validated on every
  refresh.
- **`POST /oauth/revoke`** (RFC 7009) and a `revoke_for_user` kill switch.
- **RFC 9207 `iss`** on every authorization response, including errors, plus
  the matching `authorization_response_iss_parameter_supported` advertisement.
- **Scope challenges** — the `401` now carries `scope`, and a token whose OAuth
  scope is too narrow gets a `403` with `error="insufficient_scope"` naming the
  scopes to step up to. Write tools are hidden from read-only connections.
- **Origin validation** on the MCP endpoint (DNS-rebinding protection) with a
  configurable *Allowed browser origins* setting; empty by default.

### Fixed
- **Module would not install on Odoo 19.** `security/mcp_security.xml` set
  `category_id` on `res.groups`, which no longer exists in 19: the link moved
  to `privilege_id` on the new `res.groups.privilege` model, and the privilege
  carries the `ir.module.category`. The install aborted on that record, so no
  groups, menus or views were created at all.
- **Authorization-code interception (security).** `redirect_uri` validation
  matched `http://localhost` by prefix, so `http://localhost.attacker.example`
  was accepted at registration. Hosts are now parsed and matched exactly.
- **Permanent `401` on `/mcp/v1` (interop).** The endpoint answers on both
  `/mcp` and `/mcp/v1`, but only `/mcp` was a valid token audience, so a client
  connecting to `/mcp/v1` received a token that could never authenticate. Both
  paths are now accepted audiences, and each path-scoped metadata document
  echoes its own resource identifier as RFC 9728 requires.
- **Authorization-code replay** now revokes the tokens that code already
  minted (OAuth 2.1 §4.1.3.4). Redemption is atomic, so concurrent requests
  cannot both win.
- Audience validation **fails closed**: a token with no bound resource is no
  longer accepted.

### Changed
- Scope names are now `odoo:read` / `odoo:write` (were `odoo`, `odoo.read`,
  `odoo.write`). The old names are still accepted on input.
- `mcp.oauth.client.dynamically_registered` (boolean) became
  `registration_type` (`cimd` / `dcr` / `manual`).
- `mcp.oauth.token.is_access_valid(resource=...)` became
  `is_access_valid(accepted_resources=...)`; `resource` is now required, and
  tokens carry `client_name` and `authcode_id`.
- Consent screen marks a CIMD client's self-published name as **unverified**,
  and lists the granted scopes in plain language with the token lifetime.
- The token vacuum keeps used authorization codes long enough for replay
  detection to work.

## [19.0.2.0.0] — 2026-07-28

### Added
- **OAuth 2.1 one-click connect** — self-contained Authorization Server:
  RFC 9728 protected-resource metadata, RFC 8414 server metadata, RFC 7591
  dynamic client registration, PKCE (S256), RFC 8707 audience-bound tokens, and
  refresh-token rotation. Users authorize with their normal Odoo login.
- **Streamable HTTP transport** at `/mcp` (protocol `2025-06-18`, negotiated),
  plus `/mcp/health` and a spec-compliant `401 + WWW-Authenticate` challenge.
- **Capability / Tool registry** — tools are data (`mcp.capability`, `mcp.tool`);
  new verbs ship without controller changes.
- **Aggregation tool** (`read_group`) for reports and dashboards, plus
  `get_schema`, `name_search`, `count_records`.
- **Business Context Engine** (`mcp.context`) and a curated, role-based
  **prompt library** (`mcp.prompt`) exposed as MCP prompts/resources.
- **Guided "Connect an AI Assistant" wizard** and a one-click **Settings → Fleet
  AI** page.
- **Reveal-key-once** wizard; only SHA-256 digests are stored.
- Per-user `mcp_scope_id`, rate-limit enforcement, multi-company session
  awareness, richer audit fields (model, transport, IP), OAuth token vacuum cron.
- Automated tests: engine governance, PKCE/token models, HTTP endpoints.

### Changed
- Rebranded to **Fleet AI**. Engine is now registry-driven rather than
  hard-coding a fixed tool set.
- Security expanded: MCP User/Approver/Administrator groups, record rules
  (own keys/tokens/audit/approvals), multi-company rule on keys.

### Security
- Every request executes as the authenticated user through the ORM; the scope
  can only narrow, never widen, native Odoo permissions.

## [19.0.1.0.0] — 2026-07-22

### Added
- Initial MCP endpoint (`/mcp/v1`, JSON-RPC 2.0) with hashed API keys, per-model
  scopes, field blacklists, approval-gated writes, and an audit log with token
  estimates.
