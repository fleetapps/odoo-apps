# viewcheck — offline Odoo install-time validation

Failures that are slow to find on a server and quick to find here. None of
these needs Odoo, a database, or `lxml`.

```bash
python3 tools/viewcheck/rngcheck.py     <module> [<module>…]
python3 tools/viewcheck/unsearchable.py <module> [<module>…]
python3 tools/viewcheck/accessdomains.py <module> [<module>…]
python3 tools/viewcheck/accessdiff.py   <module> [<module>…] [--ref 19.0]
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

**`accessdomains.py`** — validates every `security/ir.access.csv` domain against
the model it targets. Odoo 20's `ir.access._check_domain` `safe_eval`s each domain
and runs `Domain(...).validate(model)` at install, so a field that does not exist
fails the install; a field that exists but is a non-stored compute with no
`search=` passes validation and then fails the query. Both are reported.

**`accessdiff.py`** — proves an `ir.model.access` + `ir.rule` → `ir.access`
conversion preserves effective access. It reads the pre-port files from a git ref
(`--ref`, default `19.0`) and compares outcomes **per principal**, not per row,
because a row-by-row diff cannot decide correctness: under Odoo 19 a group's
access was an emergent product of two layers (any ACL for any *implied* group
grants the model; group rules OR together; global rules AND), whereas Odoo 20
evaluates one expression, `Domain.OR(permissions) & Domain.AND(restrictions)`,
where `Domain.OR([])` is FALSE and `Domain.AND([])` is TRUE.

It canonicalises the three spellings of "all records" — `''`, `[]` and
`[(1,'=',1)]` — because `Domain.__new__` maps all three to `_TRUE_DOMAIN`.

Pass `--extra-xml <module>/security/<file>.xml` when a module's groups are
implied by a *dependency's* security file, so the implication graph is complete
(`ai_dashboards` needs `mcp_governance_suite/security/mcp_security.xml`).

Run it on every module you convert. It caught nothing on the first three, but
only because it was written to be able to: the four perturbations it was tested
against — a dropped row, a widened operation, a dropped restriction, an emptied
domain — are each detected.
