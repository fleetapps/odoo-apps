# Apps Store listing sources

`static/description/index.html` in each module is **generated**. Edit the file
here instead, then rebuild with `tools/build_listing.py`.

## What the store does to a listing

Three separate filters, each of which silently discards work:

1. **`<style>` and `<link>` are dropped entirely.** Only inline `style=""`
   survives. Every top-downloaded third-party listing is inlined this way.
2. **Inline declarations are filtered against a property whitelist.** Anything
   outside it is dropped without warning. Notably absent: `background` (the
   shorthand - `background-color` is fine), every flex property, `box-shadow`,
   `overflow`, `border-left` and `border-right`. A gradient hero with white
   text therefore publishes as white text on white.
3. **The file is decoded as latin-1** when no charset is declared, turning em
   dashes and tick marks into mojibake.

There is also a fourth trap that is not a filter: **do not add
`<meta charset>`**. It is head-only content, so the store's parser keeps
everything after it inside `<head>` and the listing publishes completely
blank. This has happened once already.

## What the build script does about it

- resolves CSS custom properties, since `var()` cannot survive inlining
- folds `@media` breakpoints into `clamp()`, since media queries cannot either
- inlines every rule
- flattens `background` to a single opaque `background-color`, compositing
  translucent layers onto whatever they actually sit on, and picking the
  gradient stop that reads best against the element's own text
- rebuilds flex layouts as `inline-block`, recovering column counts from the
  flex-basis (leaving `display:flex` is worse than removing it: `flex-wrap`
  is stripped, so rows never wrap and run off the page)
- raises any text that fails WCAG AA against its resolved background, moving
  only lightness so brand hues survive
- escapes all non-ASCII to HTML entities, so the file decodes identically
  under any encoding
- **audits the result** and reports any property the store would strip

## Rebuilding

```bash
cp listing-src/shopify_bisync.html /tmp/index.html
python3 tools/build_listing.py /tmp/index.html
cp /tmp/index.html shopify_bisync/static/description/index.html
```

It rewrites in place, so build from a copy. Needs `premailer`, `lxml` and
`cssselect`.

**Read the warnings it prints.** They mean the published page will differ
from the source.

The two free editions live in their own repositories (`ai-dashboards-free`,
`ai-mcp`), each with its own `listing-src/`. Both publish from their **`19.0`**
branch, not `main`.
