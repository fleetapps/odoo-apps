# Changelog

All notable changes to **AI Dashboards Pro** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/); versions use
Odoo's `SERIES.MAJOR.MINOR.PATCH` scheme.

## [20.0.1.0.0] — 2026-09-24

Port to **Odoo 20**.

### Changed
- **`security/ir.model.access.csv` → `security/ir.access.csv`.** Odoo 20 merged
  access rights and record rules into a single `ir.access` model; neither
  `ir.model.access` nor `ir.rule` exists any more. The six record rules that
  lived in `ai_dashboards_security.xml` are now the domains on the matching
  permission rows.

  The rules they encoded are unchanged: you see your own dashboards plus
  anything published and shared with a group you are in, history follows the
  dashboard it belongs to, and a subscription — a standing instruction to render
  as you — stays yours alone. Administrator rows carry no domain, and permission
  rows union, so an administrator still sees everything.
- **Owl 3.** Odoo 20 replaces Owl 2 and the canvas is ported to it: `useState`
  becomes `proxy`, `useRef("root")` becomes a `signal.ref()` class field read as
  `this.rootRef()`, `static props` becomes `props = useProps(...)` (the
  compatibility layer raises on the static form), and both effects become
  `useOnChange`.

  `useOnChange` rather than Owl 3's `useEffect` deliberately. Owl 3's `useEffect`
  auto-tracks every signal its callback touches, and `drawAll()` reads most of
  the canvas state — it would tear down and rebuild every Chart.js instance on
  paging, on entering edit mode, on a rename. `useOnChange` keeps the Owl 2
  contract: an explicit, shallow-compared dependency list with the callback
  untracked.

  The redraw now depends on `rootRef()` as well as on the figures and the spec,
  and that dependency is load-bearing. Owl 2's `useEffect` fired in
  `onMounted`/`onPatched`, so the DOM was guaranteed to exist when it ran. Owl 3
  effects are not tied to the render cycle: they run once at setup and then
  whenever a tracked dependency changes, flushed in a microtask. The figures
  arrive in `onWillStart`, which completes *before* the first render, so without
  the ref the sequence was: run at setup (no data, no DOM), run again when the
  load lands (data, still no DOM), then the DOM appears and nothing re-runs —
  every chart silently blank. Depending on the ref signal makes "the element now
  exists" the trigger, which is how Owl 3 says what `onMounted` used to say.

### Notes
- **The Draft → Live lifecycle is unaffected**, which was worth checking:
  Odoo 20 made `record.update()` save by itself when a record is not in edition.
  A form's root record *is* in edition, so presentation edits still stay in
  memory until **Keep it** — the canvas bar continues to tell the truth. The
  redraw-on-spec-change also still works: Odoo's `Reactive` base now returns
  `proxy(this)` instead of Owl 2's `reactive(this)`, so reading
  `record.data[name]` inside a dependency function subscribes exactly as before.
