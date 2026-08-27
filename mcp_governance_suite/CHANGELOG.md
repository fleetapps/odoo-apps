# Changelog

All notable changes to **Odoo MCP** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/); versions use
Odoo's `19.0.MAJOR.MINOR.PATCH` scheme.

## [19.0.3.6.0] — 2026-08-27

Everything between "connected" and "useful". 19.0.3.5.0 fixed the connection
layer; a fresh install still landed on a scope that could read four `base`
models, in an app no employee could see.

### Fixed
- **`search_records` silently truncated at the row cap.** The cap is a real
  protection, but the reply carried only `count`, which was the *page* length —
  so the assistant received a full page, had nothing to tell it the page was
  full, and reported partial data to the user as the complete answer. Searches,
  `read_group` and `name_search` now fetch one row past the cap and return
  `limit` and `has_more` alongside `count`, and the tool descriptions tell the
  AI what to do when it is set. This was the highest-severity issue in the
  release: a wrong answer stated confidently is worse than a refusal.
- **`name_search` failed on every call.** Odoo 19 renamed the parameter from
  `args` to `domain` and kept no alias, so the tool raised `TypeError` and
  returned an opaque internal error — on the one tool the seeded business
  context explicitly tells the AI to use before filtering by any name.
- **The Odoo MCP menu was invisible to every employee.** Nothing implied
  `group_mcp_user`, so the root menu — gated on it — appeared only for
  administrators, and nobody finds a group they were never told about. Every
  internal user now holds the role. This is navigation, not authority: the
  read-only default scope, the permission matrix, and the user's own access
  rights are all unchanged, and an MCP call can still only ever narrow what
  that user could already do.
- **`list_models` advertised archived rows.** `line_for_model` ignores them at
  call time, so the AI was promised access every subsequent call refused. Both
  paths now agree, and `line_for_model` no longer resolves an archived row even
  under `active_test=False`.
- **A revoked-that-wasn't.** `revoke()` returned silently when the user was not
  allowed to disconnect that assistant, and the screen announced "Assistant
  disconnected." regardless. It now reports what actually happened.
- **The approval requester never learned the outcome.** Only approvers were
  subscribed to the thread, so the person whose assistant said "queued" was the
  one participant who never heard back. The queued message also names where the
  request went, instead of just saying it was queued.
- Two tests had been failing since 19.0.3.1.0 and 19.0 respectively:
  `res.users.groups_id` no longer exists in Odoo 19 (it is `group_ids`), and an
  assertion still matched refusal text that had been rewritten.
- Readiness said "12 model permission(s) set" while counting rows on archived
  scopes that nothing could reach; it now agrees with the starter prompts. The
  checklist also ends on a row rather than simply stopping, which read as a
  check that had not finished running. Stale menu paths corrected throughout.

### Added
- **Business models on install.** A `post_init_hook` opens the default scope
  onto whichever of sales, purchasing, invoicing, inventory, CRM, projects and
  employees this database actually has — impossible from a data file, which
  cannot name `sale.order` without breaking installation where Sales is absent.
  Existing databases are never changed silently: they get a readiness warning
  and a **Let it see my business data** button instead.
- **"What your assistant may do"** on the Connect screen: read-only versus
  read-and-write, the approval state, how many changes are waiting, and the
  toggle — the decision every buyer cares about, previously a checkbox on a
  scope form three menus deep. The reconnect requirement is now an alert band
  rather than grey text on a table row, because a scope change never reaches a
  live connection and everyone learns that exactly once.
- **"Run a test question as me."** One button that asks a real question through
  the real engine, past every real gate, and shows the rows. Everything else on
  that card is a precondition; this is proof. It is audited like any other
  call, tagged `Self-test` so nobody later mistakes it for an assistant.
- **Per-scope tool descriptions.** The read tools now name the models this
  connection can actually reach, so the assistant answers the first question
  instead of spending a discovery round trip on it. Permitted explicitly by
  MCP 2026-07-28 (`tools/list`: the set "MAY vary by the authorization
  presented on the request"); deliberately not applied to `server/discover`,
  whose result is cached `public` and shared across users.
- **Tool annotations** (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
  `openWorldHint`), which clients use to decide when to put a human in the
  loop — directly on-brand for a governance product.
- `list_models` now reports whether method calls are allowed and which methods
  are on the allow-list, so the AI stops guessing and being refused.

### Changed
- **Two menu items instead of four** for an employee: Connect your AI and My AI
  Activity. Connections and API Keys move to administrators — Connect already
  lists and revokes connections, and now has a **Manage API keys** button.
- **A non-administrator can no longer choose which governance scope their own
  API key runs under.** Now that every employee holds the MCP User role, that
  would have let anyone bind a key to whatever permissive scope existed and
  step around the approval gate configured for them. Not a privilege
  escalation either way — a key never exceeds its user's own rights — but the
  governance controls are the product.
- **Polling adapts.** 5s while waiting for the first connection, 30s once
  connected, and paused entirely while the tab is hidden. The QR is rendered
  server-side and is now fetched once rather than on every tick.
- **Reachability is cached for 15 minutes.** It ran on every mount of the
  screen: an outbound round trip plus three parameter writes per page view,
  per user. Re-test and the address fixes still force a fresh probe.
- Starter prompts extended to purchasing, inventory, CRM and projects, and the
  stock prompt is now gated on Inventory being installed rather than on
  `product.product`, which exists without it.
- Plain-language pass over the readiness rows, the approval message and the
  API-key refusal.

## [19.0.3.5.0] — 2026-08-27

### Fixed
- **The server advertised `http://` endpoints behind an HTTPS proxy, and kept
  going back to doing so after being fixed.** Every AI client fetches
  `/.well-known/oauth-protected-resource`, reads the authorization server out
  of it, and refuses a plain-http one (OAuth 2.1 §1.5) with an error that never
  mentions a scheme. Two separate causes compounded:
  - Odoo only applies werkzeug's ProxyFix when `proxy_mode` is on **and** the
    proxy sends `X-Forwarded-Host`. Front ends that send `X-Forwarded-Proto`
    without it — Cloudflare among them — leave the scheme as http however
    `proxy_mode` is set. The public origin is now resolved from the forwarded
    headers directly (`Forwarded`, `X-Forwarded-Proto`, `X-Forwarded-Scheme`,
    `X-Forwarded-Ssl`, `Front-End-Https`, `CF-Visitor`), so a TLS-terminating
    proxy needs no configuration to work.
  - Odoo rewrites `web.base.url` to wherever an administrator last signed in
    from unless `web.base.url.freeze` is set, so on such a deployment the
    parameter was reset to the broken http value on every admin login — the
    reason a working connector silently stopped working days later. https now
    wins from any signal and nothing can downgrade it, so a poisoned
    `web.base.url` no longer decides.
  `X-Forwarded-Host` is still honoured only under `proxy_mode`, matching
  Odoo's own trust boundary; https is never invented where nothing reported it,
  so plain-http LAN deployments keep working.
- **The Connect screen did not scroll on desktop**, cutting the page off below
  the fold — starter prompts and connected assistants were unreachable. Odoo's
  layout sets `.o_web_client > .o_action_manager > .o_action` to
  `overflow: hidden`, which outranks any single class a module can put on that
  root, so the screen's own `overflow: auto` never applied. It now scrolls its
  inner `.o_content`, the element core views scroll, which is also correct on
  small screens where Odoo inverts the pair.
- **The MCP endpoint rejected browser calls from Odoo's own web client**
  behind a TLS-terminating proxy: the Origin check compared against the scheme
  the WSGI layer saw, so an `https://` Origin failed to match and returned
  `forbidden_origin`.
- The server reported version `19.0.3.2.0` to every connected client. It is now
  read from the manifest instead of being restated in the controller.
- Settings and the Connect screen showed the raw `web.base.url`, which is how a
  user ends up pasting an address into Claude that Claude then refuses. Both
  now show the address clients actually reach.
- **The starter prompts advertised questions a fresh install cannot answer.**
  The screen promises "only prompts your current permissions can actually
  satisfy", but filtering was on write-vs-read alone, so a new install offered
  "list today's sales orders" while the seeded scope covers four `base` models
  — the click failed in the assistant and read as a broken product. Prompts are
  now matched against the models in the permission matrix, and two that work
  against any scope ("What can you do with my Odoo?") always lead, so the list
  is never empty.

### Added
- **"Fix it for me"** on the Connect screen: pins `web.base.url` to the
  detected public address and sets `web.base.url.freeze`, in one click, for
  Settings administrators. The manual route — find developer mode, find System
  Parameters, know a second `web.base.url.freeze` parameter exists at all — is
  where people gave up.
- **Readiness now catches drift before it bites.** A new warning row reports an
  address that is correct today but that Odoo will overwrite, and one for a
  proxy in front of Odoo with `proxy_mode` off. Warnings do not block
  connecting; only real blockers do.
- **The reachability test now walks the client handshake** instead of pinging
  `/mcp/health`: it fetches the RFC 9728 metadata from outside and checks that
  the sign-in address it advertises is https and names this same server. The
  old test called this deployment reachable while every client refused it.
- `Public address override` setting (`mcp_governance_suite.public_base_url`)
  for front ends that announce nothing at all. It wins over everything,
  including `web.base.url`.

### Changed
- Audience validation accepts both the http and https spelling of this server's
  own host, so tokens minted before a proxy fix keep working instead of every
  client being disconnected on upgrade.
- Consent screen said "Odoo MCP · MCP"; the duplicated word is gone, and the
  card no longer sits flush against the edge of a short popup window.

## [19.0.3.4.0] — 2026-08-07

### Changed
- Full App Store description page (`static/description/index.html`) rebuilt
  around real product screenshots — two live chat transcripts (a pie-chart
  visualization and a totals query), Connect, mobile/QR connect, Scopes,
  Model Permissions, Server Settings and Governance & Token Lifetimes —
  replacing the placeholder single-page description.
- New banner artwork; icon cropped from the same source, no third-party
  logos.

## [19.0.3.3.0] — 2026-08-06

### Changed
- **Renamed Fleet AI to Odoo MCP.** Same module, same technical name
  (`mcp_governance_suite`); only the display name and listing copy changed —
  menus, settings, security groups, cron jobs and the OAuth consent screen.
- Support now also reachable at `support@odin.ist` and `andrew@fleet.ke`,
  alongside the existing `developers@fleet.ke`.

### Added
- `doc/index.rst` — the App Store "Documentation" tab, covering installation,
  upgrade, configuration and usage.

## [19.0.3.2.0] — 2026-08-05

### Fixed
- **Write access could never actually be granted.** Turning off Read Only,
  ticking Create/Update in the permission matrix and reconnecting still
  produced a read-only assistant. The 401 challenge named `odoo:read` as the
  only scope; MCP clients treat that set as authoritative and request exactly
  it, so every token was minted read-only — and because write tools were then
  hidden from that token, the assistant never called one, never received the
  403 that asks for more, and never stepped up. The challenge now also names
  `odoo:write` whenever any active scope permits writes.
- **Dynamic client registration pinned clients to read-only.** A client that
  registered without declaring a scope was recorded as `odoo:read`, which it
  then replayed on every authorization request. It is now registered with the
  full supported set; the governance scope and consent screen still decide.
- **The consent screen could not grant more than the client asked for.** It now
  offers an explicit *"Let it create and update records"* checkbox whenever the
  governance scope allows writes, ticked by default and capped by that scope
  (RFC 6749 §3.3 lets the resource owner's instruction override the request).
- **OAuth metadata advertised `http://` behind a TLS-terminating proxy**, which
  OAuth 2.1 §1.5 forbids. The public address now wins over the scheme the WSGI
  layer saw, as long as it names the same host. Tokens issued under the old
  spelling keep validating.
- **`resultType` was missing from every result but `server/discover`.** MCP
  2026-07-28 requires it on all of them.

### Changed
- **Write tools stay listed when only the OAuth scope is missing.** Hiding them
  is what made the step-up flow unreachable. They are still hidden — and now
  explained — when the governance scope itself is Read Only, which no
  re-authorization can fix.
- **`list_capabilities` says why anything is missing.** An empty capability now
  carries `unavailable_reason` (which switch hid it and who can flip it) or
  `needs_authorization` (re-authorize with `odoo:write`), plus the connection's
  scope name, granted scopes and whether approval is required.
- **`insufficient_scope` errors** name the scope that was granted and the exact
  step that fixes it.

### Added
- **Model Permissions warns when a row's write switches are inert.** Turning on
  Create/Update for a model inside a scope that has Read Only on saves fine and
  changes nothing — the easiest way to misconfigure the module. Those rows are
  now flagged in the list and carry an explanation on the form.
- **Connections now show what an assistant can actually do** — a Read only /
  Read & write badge and the granted scope string, on both the Connect screen
  and the backend list, plus a warning when a connection's frozen scope no
  longer matches the user's current one and it needs reconnecting.

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
