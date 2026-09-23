# viewcheck — offline Odoo view validation

Two install-time failures that are slow to find on a server and quick to find
here. Neither needs Odoo, a database, or `lxml`.

```bash
python3 tools/viewcheck/rngcheck.py <module> [<module>…]
python3 tools/viewcheck/unsearchable.py <module> [<module>…]
```

**`rngcheck.py`** — reproduces `Invalid view … definition`, which is plain
RelaxNG validation. It mirrors `odoo/tools/view_validation.py::schema_valid`:
the same six view types are schema-validated (`search`, `list`, `calendar`,
`graph`, `pivot`, `activity`) and form views are not validated at all, which is
why form-only attributes survive changes that break search views. Uses
`xmllint`, which ships with macOS.

The server swallows the real cause behind a generic `ValidationError` that names
the view but never the offending attribute. This prints the attribute.

**`unsearchable.py`** — catches `Unsearchable field "x" in domain of <filter>`,
which RelaxNG cannot see: Odoo raises it from field resolution, so `rngcheck`
passes and the install still dies. It `ast`-parses each module's models for
`fields.X(...)` calls and flags any field used in a searchable position (filter
domain, `group_by` context, graph/pivot axis, `default_order`) that is computed
or related with neither `store=True` nor `search=`.

The schemas in `rng/` are copied from
`https://raw.githubusercontent.com/odoo/odoo/20.0/odoo/addons/base/rng/`.
**Refresh them on every Odoo version bump** — that is the whole point of the
check, and a stale copy validates against the wrong rules.

Prove either script is not a no-op before trusting a clean run: put
`<group expand="0" string="X">` inside a search view and confirm `rngcheck.py`
fails on it.
