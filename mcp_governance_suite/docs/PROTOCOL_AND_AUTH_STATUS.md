# Protocol & authorization — current status

**2026-08-03** · supersedes §1.2 and parts of §0/§7 of `SPEC_OAUTH_AND_UX.md` v2.

Read this before acting on the v2 spec: that document's code audit was taken
against an older tree and is stale in ways that change what to build.

---

## 1. Corrections to the v2 spec

### 1.1 The §1.2 audit was stale

v2 §1.2 describes `controllers/mcp.py` as 74 lines with no `WWW-Authenticate`,
no `.well-known` routes and no audience validation, and marks requirements
1–10 as ❌. That was not the state of the tree. Phases 1 and 2 were already
substantially built: the 401 challenge, both metadata documents, PKCE S256,
audience binding, refresh rotation and DCR all existed.

The real gaps were narrower and different from the ones listed — see §2.

### 1.2 "Bump `PROTOCOL_VERSION` to 2026-07-28" was actively wrong

v2 §1.2 treats the protocol bump as a one-line constant change. It is not.
Revision `2026-07-28` **removed the `initialize` handshake**, removed
protocol-level sessions, removed the GET stream, and made `server/discover` a
mandatory RPC. Version, client identity and capabilities now travel as
per-request `_meta` plus mirrored HTTP headers.

Changing only the constant would have advertised a revision we do not speak.
The spec's own compatibility matrix scores that combination as **Fails**.

What was built instead: a **dual-era** server. An `initialize` call selects
legacy semantics; modern `_meta` selects modern. Both work on the same
endpoint. This is the behaviour the spec sanctions for servers that need to
serve today's *and* yesterday's clients.

| Era | Revisions | Opens with |
|---|---|---|
| Modern | `2026-07-28` | per-request `_meta` + `MCP-Protocol-Version` header |
| Legacy | `2025-06-18`, `2025-03-26` | `initialize` handshake |

Note also that `2025-11-25` exists between the two and is **not** implemented;
it is a legacy-era revision, so clients pinned to it fall back to `initialize`
and negotiate down to `2025-06-18`.

### 1.3 What v2 got right

Verified against the live specification on 2026-08-03:

- There is no OAuth 2.2. The MCP authorization spec cites **OAuth 2.1**
  (draft-13) throughout. ✅
- The current revision really is **`2026-07-28`**. ✅
- **CIMD is `SHOULD`; DCR is `MAY` and explicitly deprecated.** ✅ — building
  CIMD first was the right call.
- `scope` on the 401 challenge, `403 insufficient_scope`, and RFC 9207 `iss`
  are all `SHOULD`. ✅
- `offline_access` must not appear in protected-resource `scopes_supported`
  (it *may* appear in authorization-server metadata). ✅

---

## 2. What was actually wrong in the code

Two of these were live defects, not missing features.

| # | Defect | Impact |
|---|---|---|
| 1 | `_is_valid_redirect` matched `http://localhost` by **prefix** | `http://localhost.attacker.example` passed. With open dynamic registration an attacker registers a client with that callback, sends a victim to `/oauth/authorize`, and collects the authorization code on their own domain — and holds the PKCE verifier, having started the flow. **Authorization-code interception.** |
| 2 | Canonical resource was only `<base>/mcp`, but the endpoint answers on `/mcp` **and** `/mcp/v1` | A client connecting to `/mcp/v1` gets a token whose audience never matches → **permanent 401 with no diagnostic**. The path-scoped RFC 9728 document also returned a `resource` that did not match the URL it was fetched from, which a conforming client rejects. |
| 3 | Authorization-code replay marked the code used but left issued tokens live | Violates OAuth 2.1 §4.1.3.4. Redemption was also non-atomic. |
| 4 | Audience check was `if resource and self.resource and ...` | **Fail-open**: a token with no bound resource passed any audience check. |
| 5 | No `Origin` validation; CORS hardcoded to `*` | Transport spec says servers **MUST** validate `Origin` (DNS rebinding). |
| 6 | No CIMD | The `SHOULD`-level registration mechanism, and the thing that makes "no client ID or secret" true. |
| 7 | No `scope` on 401, no 403 `insufficient_scope`, no `iss`, no revocation endpoint | Spec-level `SHOULD`s; without them step-up authorization cannot work. |

All seven are fixed. See `CHANGELOG.md` for the 19.0.3.0.0 entry.

---

## 3. Design decisions worth knowing

**Both endpoint paths are valid audiences.** `/mcp` is canonical and is what we
advertise; `/mcp/v1` is accepted because the endpoint genuinely answers there
and clients are told to use the most specific URI they can. Each path-scoped
metadata document echoes its own identifier. Anything else is refused.

**Scope names changed to `odoo:read` / `odoo:write`** to match the spec's wire
format. The old `odoo`, `odoo.read`, `odoo.write` still parse, and `odoo` maps
to read — so an upgraded token is never silently *widened*.

**OAuth scope now gates writes**, in the engine, on top of the governance
scope. That is what makes a 403 actionable: the client learns it needs
`odoo:write` and can step up, instead of getting a flat denial. Write tools are
also hidden from `tools/list` for read-only connections, so the model does not
keep trying them.

**API-key connections carry no OAuth scope** and are governed by `mcp.scope`
alone — they never receive a scope challenge.

**CIMD metadata is attacker-controlled** and is treated that way: stored,
displayed as *unverified* on the consent screen, and never a source of
authority. Authority comes from the user's consent and exact `redirect_uri`
matching.

**CIMD fetching is SSRF-hardened**: HTTPS with a path component only, DNS
resolved and checked against private/loopback/link-local/reserved ranges,
redirects refused, 5s timeout, 64 KB cap, cached with a TTL. Residual risk: a
DNS-rebinding window between our check and `requests`' own resolution. The
timeout, byte cap and redirect refusal bound it; closing it fully needs
IP-pinned TLS, which is not worth the complexity here.

---

## 4. Verification performed

Executed here:

- **12 pure-function tests** against the real module source (redirect
  validation incl. every loopback lookalike, CIMD shape, SSRF guard incl.
  `169.254.169.254`, scope normalisation, base64 header sentinel, PKCE against
  the **RFC 7636 Appendix B test vector**). All pass.
- **View/model cross-check**: 205 `<field>` nodes across 18 models, walking
  sub-views into their comodel. No unknown fields.
- **XML id cross-check**: every `ref`, `action`, `parent` and `%(…)d` reference
  resolves; every manifest data file and asset exists.
- **OWL template lint**: no Python operators in JS expressions, every
  `t-foreach` has a `t-key`, every `this.*` the template calls exists on the
  component, and the `static template` name matches a `t-name`.

Not executed — no Odoo runtime in this environment:

- `tests/test_oauth.py`, `test_http.py`, `test_engine.py`,
  `test_permissions.py`, `test_connect.py`.
- The Connect screen has **never been rendered in a browser**. The template and
  component are statically consistent, but mounting, polling and the clipboard
  path are unverified.
- Nothing has been deployed to `sandbox.odin.ist`.

**Before trusting any of this: install the module, run the test suite, open
Odoo MCP → Connect your AI, then re-run the §7 phase gates against a live
instance.**

---

## 5. What remains

Phases 1–4 of the v2 spec are complete. Untouched:

| Phase | Work | Note |
|---|---|---|
| 5 | `search_records` / `get_schema` / `create_record` / `call_method` exist | Missing: "smart fields" auto-selection, `mcp_max_smart_fields`. This is the top complaint from Odoo MCP practitioners — models with hundreds of fields produce poor answers. |
| 6 | Dashboards (§5.3), document ingestion, CRM | Reuse `ai_bill_ocr_community` / `po_so_ai_capture` for extraction rather than reimplementing. |
| 7 | Permission proposals (§5.4) | Highest risk; behind approvals. |
| 8 | DCR | Already built (it predates the CIMD work). Now correctly labelled deprecated. |

### Phase 3–4 notes (built 2026-08-03)

**The matrix is `mcp.scope.line`, promoted — not a new `mcp.model.access`.**
The v2 spec said "replacing/extending `mcp.scope.line`". Creating a second
model would have meant two sources of truth for the same decision, so the
existing one gained `active`, `can_call_methods`, `allowed_methods` and a
top-level editable-list view instead. One source of truth, no migration of
existing scopes.

**Method calls are gated four times**, because this is the verb that turns a
read-only assistant into one that can post a journal entry: the matrix bit, a
per-model allow-list of exact names, a global denylist of ORM/privilege verbs,
and the acting user's own rights when it runs. The bit alone grants nothing —
an empty allow-list permits nothing. The allow-list is re-checked at approval
time, so approving a stale request cannot resurrect a revoked permission.

**"Last Modified / Modified By" are `write_date` / `write_uid`**, not
`mail.thread` tracking. They give exactly the two columns §4.2 asks for at zero
cost. Full per-field change history would need `mail.thread` on the line model
and a chatter, which does not belong in an editable matrix row — worth
revisiting only if an auditor asks for it.

**The Connect screen is an OWL client action**, not a form view, purely because
of the live-status card: the user pastes the URL, authorises in another window,
and the page must flip to *Connected* on its own. Everything else on the screen
is server-rendered data from a single `mcp.connect.get_state()` call, so the
component stays a thin renderer and the logic stays testable in Python.
`get_state()` deliberately performs **no** network I/O — it is polled every 5s,
and reachability testing lives in its own explicitly-invoked method.

Also still open:

- **`private_key_jwt`.** ChatGPT's signed-client-assertion path needs it. The
  `jwks_uri` from CIMD metadata is stored but assertions are **not** verified,
  so it is deliberately **not** advertised in
  `token_endpoint_auth_methods_supported` — advertising an unimplemented auth
  method is worse than omitting it. Required before claiming ChatGPT support.
- **§10 prerequisite is unaddressed and unverifiable from here.**
  `odoo3/odoo.conf` reportedly commits live Postgres credentials and
  `admin_passwd = admin` in a public repo, with `list_db = True`. That repo is
  not in this working tree. Rotate before issuing tokens against that database
  — an OAuth server on a compromised database is theatre.
- **The three-client test matrix (§2.1) has not been run.** Claude Desktop,
  ChatGPT Developer Mode, and VS Code/Cursor each exercise a different
  registration path. CIMD in particular is unverified against a live connector.
