# Changelog

All notable changes to **Odoo Shopify Sync** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/); versions use
Odoo's `SERIES.MAJOR.MINOR.PATCH` scheme.

## [20.0.1.0.0] — 2026-09-24

Port to **Odoo 20**. No connector behaviour changes — the Shopify API stays
pinned at `2026-07` and every sync path is untouched. Two Odoo platform
rewrites, both of which fail silently rather than loudly.

### Changed
- **`security/ir.model.access.csv` → `security/ir.access.csv`.** Odoo 20 merged
  access rights and record rules into a single `ir.access` model;
  `ir.model.access` and `ir.rule` no longer exist. The 25 access rows keep their
  ids and carry their operations as a compact `crud` string, and the five
  multi-company rules from `connector_security.xml` become rows in the same file.

  Those five keep their `rule_*_company` ids because they are the same decision,
  and they deliberately carry **no group**: in `ir.access` a group-less row is a
  *restriction* that intersects for everybody, administrators included — exactly
  what a global `ir.rule` did. Giving one of them a group would turn
  multi-company isolation into an extra grant, because rows *with* a group are
  permissions and permissions union.
- **Owl 3.** Odoo 20 replaces Owl 2, and `useState` is gone with no shim in the
  compatibility layer, which additionally raises on `static props`. All three
  components move to `proxy()` for state. The two client actions (Dashboard,
  Sync Dashboard) now declare no props — they never read the ones the action
  service hands them, and `static props = { "*": true }` existed only to quiet
  Owl 2's validator. The Sync Health view widget does read `props.record`, so it
  declares `props = useProps({ ...standardWidgetProps })` as a class field.

## [19.0.1.7.0] — 2026-08-07

### Changed
- **Renamed "Shopify Connector - Two-Way Sync" to Odoo Shopify Sync**, matching
  the name used everywhere else (README, docs, App Store listing). Same
  technical name (`shopify_bisync`); listing metadata only.
- `author` corrected from a placeholder value to `Fleet`.
- Manifest `description` rewritten to match the depth of the README and the
  App Store description page.

### Added
- `README.md` and `doc/index.rst` (the App Store "Documentation" tab) — both
  new; the module previously shipped without either.
- `CHANGELOG.md` (this file).
- Payout reconciliation documented for the first time — the feature has
  existed in code (`models/payout.py`, Shopify Payments only) but was never
  mentioned in the README, docs, or listing page.
- Full App Store description page (`static/description/index.html`) rebuilt
  around real product screenshots (What Syncs, Stores, Backfill, Conflict
  Log, Mismatch Log, Payouts, Sales Analysis, Sync Health, Dashboard),
  replacing placeholder SVG screenshots.
- New icon and banner artwork.
