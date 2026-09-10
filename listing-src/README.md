# Apps Store listing sources

`static/description/index.html` in each module is **generated**. Edit the file
here instead, then rebuild.

The Odoo Apps Store sanitises description HTML: it drops `<style>` and `<link>`
entirely, keeping only inline `style=""` attributes. Every top-downloaded
third-party listing on the store is inlined this way. It also decodes the file
as latin-1 when no charset is declared, which is what turned em dashes into
`â` and middots into `Â·` on the published pages.

`tools/build_listing.py` handles both: it resolves CSS custom properties,
folds `@media` breakpoints into `clamp()` (the store keeps no media queries),
inlines every rule, and escapes all non-ASCII to HTML entities so the file
decodes identically under any encoding.

```bash
python3 tools/build_listing.py listing-src/shopify_bisync.html
```

It rewrites the file in place, so build from a copy:

```bash
cp listing-src/shopify_bisync.html /tmp/index.html
python3 tools/build_listing.py /tmp/index.html
cp /tmp/index.html shopify_bisync/static/description/index.html
```

Needs `premailer`, `lxml` and `cssselect`.

The script reports anything it could not carry over - leftover CSS, unresolved
`var()`, selectors it could not translate, and `@media` rules with no base rule
to fold into. Those warnings mean the published page will differ from the
source, so read them.

The two free editions live in their own repositories
(`ai-dashboards-free`, `ai-mcp`) and each keeps its own `listing-src/`.
