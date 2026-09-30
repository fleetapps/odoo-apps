# Changelog

All notable changes to **Odoo Shopify Sync** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/); versions use
Odoo's `19.0.MAJOR.MINOR.PATCH` scheme.

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
