# Access Manager Pro

No-code access control for Odoo. One **Access Profile** targets a set of users,
groups and/or companies and declares what they may see and do across menus,
models, fields, view elements, records and chatter — enforced server-side where
it matters and reflected in the UI so users never see what the server would
refuse.

Built against the official Odoo 19 developer documentation; the engine is
version-defensive and runs on **Odoo 17, 18 and 19** (primary target: 19/18).

---

## New in 19.0.2.0.0

* **Access Management dashboard** — an OWL client action (all charts drawn as
  inline SVG with axis ticks, gridlines, legends and hover tooltips — no
  charting dependency). The chart set follows common IAM / access-review
  dashboard practice: 9 KPI cards, restriction-type donut, 6-month creation
  trend, **restriction-composition** stacked bar, **users-by-restriction-load**
  histogram (spot outliers), **most-restricted-users** ranking, **rules-by-
  company**, **upcoming-expirations** buckets, restricted-model ranking,
  config-score gauge + insights, a colour-coded model×action heatmap, a
  per-user access inspector, and a quick-actions bar. `models/access_dashboard.py`
  provides the data; `static/src/{js/dashboard,xml,scss}` render it.
* **Scheduling & auto-revocation** — per-profile validity window (`date_start`
  / `date_end`) and a daily **time window with timezone**; expired profiles are
  ignored immediately (`_profiles_for`) and deactivated by an hourly cron
  (`data/access_cron.xml`).
* **Sensitive-data masking** — a `masked` field mode enforced on the client read
  path (`web_read`), so the real value stays intact server-side.
* **Hide whole view types** — remove kanban/pivot/graph/calendar/activity/map
  from the view switcher via `ir.actions.act_window.read` (list/form always
  kept; runs under the sudo action-load read).
* **Hide favourites / spreadsheet** — global body-class CSS from `session_info`.
* **Hierarchy access** — a domain-rule mode that restricts a user field to the
  current user (± subordinates via the employee hierarchy), baked per-user at
  cache-build time.
* **JSON rule import/export** — `action_export_json` downloads a portable
  bundle (natural keys); the import wizard resolves references and skips/logs
  anything missing on the target database.

---

## Architecture

Everything hangs off one cached, read-only data structure so the hot paths
(`get_view`, `_search`, `create/write/unlink`, menu loading) never run a
database search per request.

```
access.manager.profile          ← configuration record (targeting + switches)
 ├─ access.manager.model.rule    ← per-model buttons, chatter, reports/actions
 ├─ access.manager.field.rule    ← per-field invisible/readonly/required/…
 ├─ access.manager.element.rule  ← buttons/tabs/kanban/filters/groupby/xpath
 └─ access.manager.domain.rule   ← conditional record restrictions (r/c/w/u)

access.manager.profile._get_access_config()   ← @ormcache(uid, company)
        returns a frozen plain-data dict; rebuilt only when a profile or rule
        changes (access.manager.cache.mixin clears the registry cache).
```

### Extension points used (all official, all stable across 17→19)

| Concern | Hook | File |
|---|---|---|
| View arch mutation | `Base.get_view()` | `models/base_view.py` |
| Read-only user, export, archive | `Base.create/write/unlink`, `export_data` | `models/base_access.py` |
| Record (domain) read filtering | `Base._search()` | `models/base_access.py` |
| Menu hiding (per-user safe) | `ir.ui.menu._load_menus_blacklist()` | `models/ir_ui_menu.py` |
| Reports & contextual actions | `ir.actions.actions.get_bindings()` | `models/ir_actions_actions.py` |
| Developer mode + client hints | `ir.http.session_info()` | `models/ir_http.py` |
| Login blocking | `res.users._check_credentials()` | `models/res_users.py` |

> **Why `_load_menus_blacklist` and not `_visible_menu_ids`?**
> `_visible_menu_ids` is `@ormcache`d on the *group set*, so subtracting
> per-user menus there would leak between two users who share the same groups.
> `_load_menus_blacklist` runs inside `load_menus`, whose cache is keyed on the
> *user id* — correct and per-user.

### Recursion & performance safety
* Compiling the config searches the profile tables; those reads carry an
  `access_manager_skip` context flag so the ORM overrides short-circuit and can
  never recurse into the engine while it builds its own cache.
* Every override's first act is the cheap gate: superuser / admin / skip →
  return immediately; unrestricted user → return immediately.
* Domain checks are gated by a `frozenset` membership test before any work.

---

## Cross-version notes (Odoo 17 / 18 / 19)

* **List root tag.** 18/19 use `<list>`; 17 uses `<tree>` (and *rejects*
  `<list>`). The **engine** handles both (`arch.tag in ('list','tree')`). This
  module's *own* two view files use `<list>` (18/19). To install on **17**,
  swap `<list>`→`<tree>` in `views/access_profile_views.xml` — the same
  mechanical change Odoo's `upgrade_code` performs.
* **Chatter** is the `<chatter/>` tag on 17/18/19; whole-chatter hiding also
  handles a legacy `<div class="oe_chatter">`.
* **Groups** are read via `_get_group_ids()` when present (19) and fall back to
  `groups_id.ids` (17/18).
* **Domain negation** uses an inlined `normalize_domain` so it does not depend
  on the `odoo.osv.expression` → `odoo.fields.Domain` reshuffle.

---

## Enforcement matrix

| Feature | UI (view/menu) | Server-side |
|---|---|---|
| Hide menu / sub-menu / Apps menu | Yes | n/a (menu is UI) |
| Field invisible / readonly / required | Yes | export strips invisible fields |
| No quick-create / no open link | Yes | — |
| Hide Create / Edit / Delete / Duplicate | Yes | Create/Edit/Delete via ACL; read-only user hard-blocks |
| Hide Import / Export | Yes | Export blocked in `export_data` |
| Hide Archive / Unarchive | hidden (client) | Yes — blocks `active` toggle |
| Buttons / tabs / kanban / filters / group-by | Yes | — |
| Reports & contextual actions | Yes (`get_bindings`) | — |
| Whole chatter | Yes | — |
| Send message / Log note / Activities / Followers | Yes (per-model + global, component patch) | — |
| Record (domain) read incl. pivot/graph aggregates | Yes (list filtering) | Yes (`_search` + `_read_group`) |
| Record (domain) create/edit/delete | — | Yes — raises `AccessError` (unless *soft*) |
| Read-only user | Yes | Yes — hard-blocks writes |
| Disable developer mode | Yes (`session_info`) | best-effort |
| Disable login | — | Yes (`_check_credentials`) |
| Block XML-RPC / external API | — | Yes (`type='xmlrpc'` gate) |

**Known limitations (documented, not bugs):**
* Domain *read* filtering hooks `_search`; `read_group`/pivot/graph aggregates
  are not filtered. For hardened isolation add a native record rule alongside.
* A restricted comodel shown *embedded* in another model's form is not mutated
  (own-model scoping is intentional to avoid rewriting the wrong view).
* Chatter button hiding relies on the stable `o-mail-Chatter-*` classes.

---

## Security model
* All configuration models are **admin-only** (`ir.model.access.csv`); the
  engine reads them via `sudo()`, so end users need no direct access.
* The delegated **Access Manager / Administrator** group is granted
  automatically to every Settings/System administrator (they can never lock
  themselves out) and never leaks system privileges to other members.

---

## Tests
`tests/test_access_manager.py` covers field mutation, list columns, conditional
attributes, model switches, admin bypass, read-only enforcement, archive block,
domain read/hard/soft behaviour, menu blacklisting and export blocking.

```bash
odoo -i access_manager_pro --test-enable --stop-after-init
```

## VERIFY-ON-BUILD
* QA `get_view` mutations across list/form/kanban/search on the 5 busiest
  models of each target install.
* Confirm the `o-mail-Chatter-*` / `o_field_property_add` class names on the
  exact Odoo build in use (they are stable but edition/theme can vary).
* For Odoo 17, apply the `<list>`→`<tree>` swap noted above.
