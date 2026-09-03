# Launch page 1 — primary / desire-led

**Ad audience:** Odoo users, owners, ops managers searching "Odoo AI", "Odoo
ChatGPT", "connect Claude to Odoo". Warm-ish. They already want this.
**Job:** make the value obvious in ten seconds, make setup look small, remove
the security objection before it hardens.
**Structure:** Julian Shapiro's hero → proof → CTA → features → repeat CTA,
sequenced with Harry Dry's ten steps.
**Headline test:** if a visitor reads only the H1, do they know exactly what is
being sold? Yes — the product, the input, the outcome.

Section labels in `SMALL CAPS` map to your HTML blocks.

---

## `HERO`

### H1
# Ask your Odoo anything, from Claude or ChatGPT

### Subhead
Paste one URL into your assistant and ask questions in plain English — last
month's revenue, which deliveries are late, who your top customers are. It runs
inside Odoo, answers as *you*, and records every question it was asked.

### Hero visual
`[SCREENSHOT: a Claude conversation, side by side with the Odoo record it
answered from. Show the actual answer to an actual question — "What were our
sales last month?" with a real breakdown. Not an abstract illustration, not a
dashboard mock-up. The product, mid-use.]`

### Primary CTA
**Get it on the Odoo App Store — $199**
Sub-label: *One-time. Odoo 19. Installs in a minute.*

### Secondary CTA
**Try the live demo** → `sandbox.odin.ist`

### Trust strip
`[PROOF — REPLACE]`
*This slot wants the strongest true thing you have. In order of preference:
App Store rating and review count; number of installs; a named customer; "built
by an Odoo partner since 20XX". If you have none of these yet, delete the strip
entirely — an empty proof slot is better than a manufactured one, and this
audience checks.*

---

## `HOW IT WORKS` — three steps

Intro line: **Setup is one copy and one paste.**

**1. Install the app**
From the Odoo App Store, into your existing Odoo 19. No separate server, no
middleware, no extra service to run or pay for.

**2. Copy your server URL**
The Connect screen shows one address and checks everything that has to be true
before it will work — public HTTPS, reachability, permissions. Anything wrong
gets a plain-English explanation and a button that fixes it.

**3. Paste it into your assistant**
Claude, ChatGPT, Cursor, VS Code, or any MCP client. Sign in with your normal
Odoo login — SSO and 2FA included — and click Allow. VS Code and Cursor install
in one click.

`[SCREENSHOT: the Connect screen with the URL, the QR code and the green
readiness checks visible.]`

**Micro-copy under the steps:** No API keys to generate. No config files to
edit. No `mcp-remote` wrapper.

---

## `WHAT YOU CAN ASK`

Intro: **The questions people actually ask on day one.**

Present as cards or a two-column list. Each is a real question the tools can
answer; keep them concrete and un-rounded.

- *"What were our sales last month, broken down by salesperson?"*
- *"Which deliveries are late, and who's the customer on each?"*
- *"Show me unpaid invoices over 60 days."*
- *"Which products are below 10 units in stock?"*
- *"Who are my top 10 customers by revenue this year?"*
- *"What did we buy from each vendor this quarter?"*
- *"Draft a quotation for Deco Addict with these five lines."*
- *"Read this supplier PDF and create the contacts."*

Closing line: The last two only work if you turn writing on. It is off by
default, and it stays off until an administrator decides otherwise.

---

## `WHY IT'S SAFE` — objection handled early, not buried

**H2: It runs as you — so it can't see anything you can't**

Every request executes as the signed-in Odoo user. Your access rights, record
rules, field permissions and company access all apply underneath. A salesperson's
assistant sees their pipeline. It cannot open payroll, because their account
cannot open payroll.

There is no service account, no shared admin token, no second permission system
to keep in sync with the first.

**Three sub-points, as a row:**

- **Read-only by default.** Writing is off until someone turns it on. The safe
  setting is what you get by not deciding.
- **Approval before changes.** Turn writing on and every change can become a
  request a person approves — the AI drafts, a human commits.
- **Every question logged.** One row per call: who asked, which tool, which
  records, when, how long, and whether it was allowed.

Link out: *The full governance model →* `[link to launch page 2 or article 2]`

---

## `WORKS WITH`

**H2: Any MCP client, including the one you already use**

| Client | Setup |
|---|---|
| **Claude** | Paste the URL under Settings → Connectors |
| **ChatGPT** | Paste the URL under Connectors (Developer Mode, paid plan) |
| **VS Code** | One-click install button |
| **Cursor** | One-click install button |
| **Anything else** | Standard MCP — remote HTTP, OAuth discovered automatically |

Under the table: Built on the open Model Context Protocol, speaking both the
current revision and the older handshake-based ones — so connectors built
against either generation keep working.

---

## `BUILT INTO ODOO, NOT BESIDE IT`

**H2: No middleware. No second thing to run.**

Most connectors are a separate process you host, monitor, secure and upgrade
alongside Odoo. This one is an Odoo module. It runs in your Odoo, deploys with
your Odoo, backs up with your Odoo, and needs nothing installed beyond what your
server already has.

Three points:
- **Nothing extra to host.** No Node process, no proxy, no container.
- **Nothing extra to secure.** One system to patch, not two.
- **Nothing extra to pay for.** No per-seat pricing, no metered calls, no
  subscription.

`[SCREENSHOT: the permission matrix — one row per model, one column per
operation. It is the most convincing screenshot in the product; it makes
"governed" concrete in a way no paragraph does.]`

---

## `PRICING`

**H2: $199, once**

One-time purchase on the Odoo App Store. Unlimited users. Unlimited questions.
No metering, no per-seat cost, no subscription. Your AI usage is billed by your
AI provider, as it already is.

**CTA: Get it on the Odoo App Store — $199**

---

## `FAQ`

Answer objections in the reader's own words. Keep answers to two or three
sentences, and concede where conceding is true.

**Do I need an OpenAI or Anthropic API key?**
No. You connect the assistant you already pay for — your Claude or ChatGPT
subscription — and sign in with your Odoo login.

**Does my Odoo data get sent anywhere?**
Only to the assistant you connect, in answer to questions you ask, exactly as if
you had copied it out of Odoo yourself. Nothing is sent to us and nothing is
stored outside your Odoo.

**Can it change or delete records?**
Not unless you turn that on. It ships read-only. When you do enable writing you
choose which models, which operations, and whether each change needs human
approval first.

**What if it gets something wrong?**
Every call is logged with who asked and what it touched, so you can find it.
With approval on, wrong answers never reach your data — they sit in a queue for
someone to reject.

**Which Odoo versions?**
Odoo 19. Community and Enterprise.

**Does it work with Odoo Online / SaaS?**
`[VERIFY BEFORE PUBLISHING — Odoo Online restricts custom modules. Answer
accurately for Online, Odoo.sh and self-hosted separately, or omit this
question. Do not guess: getting this wrong generates refunds.]`

**What if my Odoo isn't on the public internet?**
Assistants like Claude and ChatGPT need to reach your server, so it has to be
publicly reachable over HTTPS. The Connect screen tests this and tells you
plainly if it isn't.

**Can I limit it to one team?**
Yes. Permissions are per user, and the governance scope is set per user or
database-wide.

---

## `SECOND CTA`

**H2: Ask your first question in about five minutes**

**Get it on the Odoo App Store — $199**
*Or try the live demo first →*

---

## `FOUNDER'S NOTE`

Harry Dry's formula: put yourself in their shoes → name their problem → take
ownership → show the ending. Keep it short and first-person. Replace the
bracketed parts with your own.

> Every Odoo I have worked on had the answer somewhere. Finding it meant knowing
> which report, which filter, which view — so the people who needed the number
> asked the person who knew where it lived, and waited.
>
> The connectors I tried either wanted admin credentials for the whole database
> or needed a second server to babysit. Neither was something I would put in
> front of a client.
>
> So we built this one the other way round: it runs inside Odoo, it runs as the
> person asking, and it cannot do anything they could not already do by hand.
> Then we spent most of our time on the boring half — what happens when it
> writes, and how you prove afterwards what it did.
>
> If it does not do what this page says, email me.
>
> **[Name]**, [role] — [email]

---

## Notes for the build

- **Above the fold must carry:** H1, subhead, product screenshot, price, primary
  CTA. NN/G's research puts the decision to stay or leave inside the first ten
  seconds, with an 84% difference in attention above versus below the fold. If
  something has to be pushed down, push the trust strip, not the price.
- **Price above the fold on purpose.** $199 one-time is an *advantage* against
  subscription competitors. Hiding it invites the assumption that it is a
  subscription.
- **Every screenshot shows the product in action.** Both frameworks are
  unanimous, and the permission-matrix shot is the one that does the most work —
  it makes "governed" visible instead of asserted.
- **No "empower", "unlock", "seamless", "revolutionise", "supercharge".** NN/G
  measured a 27% usability gain from objective language over promotional
  language, because readers have to filter hyperbole before they can read the
  fact.
- **Repeat the CTA exactly.** Same words both times. Different wording reads as
  a different offer.
- **Verify the Odoo Online answer before this goes live.** It is the one FAQ
  that can generate refunds if wrong.
