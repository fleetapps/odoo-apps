# How these were written, and the two rules you must not break

Five pieces: three articles, two launch pages. Content, hierarchy and structure
only — no HTML, no styling. Section labels in `SMALL CAPS` on the launch pages
map to blocks you can drop into your own template.

---

## The two rules

**1. Every social-proof slot is a placeholder. Do not ship them as written.**

Both landing-page frameworks put social proof above the fold, and both are
right. But I will not invent a testimonial, a star rating, a customer logo, an
install count or a performance statistic for a page you are going to spend
money driving traffic to. Fabricated proof is the fastest way to lose a
technical buyer, and on a governance product it is self-defeating — you are
selling trustworthiness.

Every one is marked `[PROOF — REPLACE]` with a note on what kind of proof fits
that slot. Until you have real ones, delete the block rather than soften it.
A page with no testimonials reads as new. A page with invented ones reads as
dishonest, and the audience for this product is exactly the audience that
checks.

**2. Everything else is verified against the code.**

Every capability claim — OAuth 2.1 with PKCE, per-model permissions, the
approval gate, the audit row per call, running as the signed-in user, the
protocol revisions, the price — is checked against the current module. Where I
was not certain a claim held, I cut it rather than hedge it. If you edit, keep
that standard: one wrong technical claim on this page costs more than three
missing features.

---

## The research these are built on

**How people actually read** — Nielsen Norman Group's foundational study found
79% of users scan any new page and only 16% read word-by-word. Rewriting the
same content three ways measured: concise text +58% usability, scannable layout
+47%, objective language +27%, all three together **+124%**. The same research
found users "detested marketese" — promotional language costs comprehension
because readers have to filter the hyperbole before they can read the fact.
That is why these drafts are flat, specific and short-sentenced. It is not a
stylistic preference.
<https://www.nngroup.com/articles/how-users-read-on-the-web/>

**Headlines** — NN/G's five rules: make them work out of context, tell the
reader something useful, avoid cute or faddish vocabulary, omit nonessential
words, front-load the strong keywords. Their own example of the failure mode:
"Get the most bang for your buck with XYZ" versus "Increase productivity by 24%
with XYZ." Every headline below is front-loaded and survives being read alone
in a feed.
<https://www.nngroup.com/articles/headings-pickup-lines/>

**The ten-second window** — users leave a page in 10–20 seconds unless the value
proposition lands inside the first ten. Attention above the fold versus below
differs by 84%. Both launch pages therefore answer *what is this, who is it
for, what does it cost me* before anything else.
<https://www.nngroup.com/articles/how-long-do-users-stay-on-web-pages/>

**Article structure** — Julian Shapiro's handbook: the trifecta of a captivating
opening, a section of genuine surprise, and an ending that justifies the read;
"dopamine counting" to find the flat stretches; the peak-end rule, so the
strongest insight is withheld rather than spent in paragraph two; and sentences
"a thirteen-year-old could follow." Also his tweet test — if the whole piece
compresses to one tweet without loss, publish the tweet instead. All three
articles survive it.
<https://www.julian.com/guide/write/rewriting>

**Landing page structure** — Julian Shapiro's formula, which is the spine of
both pages: **Purchase Rate = Desire − (Labor + Confusion)**. His litmus test
for a headline: if a visitor reads only that line, do they know exactly what you
sell? His feature rule: header, paragraph, and an image of *the product in
action*, carrying a running narrative back to the hero.
<https://www.julian.com/guide/growth/landing-pages>

**Landing page sequence** — Harry Dry's ten steps, which is where the FAQ,
second CTA and founder's note come from: title, subtitle, visual, social proof,
CTA above the fold; features and objections, social proof, FAQ, second CTA,
founder's note below it. His objection rule: answer them *in the customer's own
words*.
<https://marketingexamples.com/landing-page/guide>

---

## What each piece is for

| # | Piece | Reader | Job |
|---|---|---|---|
| 1 | Row caps make your AI confidently wrong | Engineers building or buying MCP servers | Earn credibility on merit. Give away a real insight; sell nothing. |
| 2 | An AI connector should never have its own ERP login | The person who has to approve it | Convert the sceptic. Turn the security objection into the reason to buy. |
| 3 | The integration installed fine | Odoo partners, product people | Differentiate. Show the care that a feature list cannot. |
| 4 | Launch page — primary | Odoo user or owner who wants AI | Desire-led. Broad ad traffic. |
| 5 | Launch page — governance | Ops lead, CTO, partner | Objection-led. Runs against a colder, more sceptical ad set. |

Articles 1 and 3 are deliberately not about the product. They are about problems
the product happens to solve, which is what makes them worth a technical
reader's time and worth a link from someone who does not care about your app.
Article 2 is the one that sells, and it sells by conceding things.

The two launch pages are an A/B pair, not a funnel: one leads with what you get,
one leads with what you are afraid of. Run them against different ad sets rather
than sequencing them.

---

## Things to fill in before publishing

- `[PROOF — REPLACE]` blocks — every one
- `[SCREENSHOT: …]` — the visual notes say what to show; the frameworks are
  unanimous that it must be the product in action, not an illustration
- The live demo URL, if you want it public: currently
  `sandbox.odin.ist/odoo/settings#mcp_governance_suite`
- Your byline and one line of credibility for the founder's note
