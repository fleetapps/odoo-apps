# Launch page 2 — governance / objection-led

**Ad audience:** the person who has to approve it. Ops lead, CTO, finance
director, Odoo partner evaluating for clients. Colder and more sceptical than
page 1's traffic. They are not searching "Odoo AI" — they are being *asked* for
AI and looking for a reason to say no.
**Job:** lead with the fear, resolve it with mechanism, and make saying yes the
low-risk option.
**Run against a different ad set than page 1. This is not a follow-up page —
it's the other half of an A/B pair.**

The rhetorical move throughout: **concede first, then answer.** A sceptic who
sees their own objection stated accurately reads the rest differently.

---

## `HERO`

### H1
# Let your team use AI on Odoo without giving it the database

### Subhead
Every request runs as the person who asked — bounded by their own Odoo
permissions, narrowed further by a policy you set, and recorded in a row with
their name on it. No service account. No shared admin token. Read-only until you
decide otherwise.

### Hero visual
`[SCREENSHOT: the permission matrix — one row per model, columns for read,
create, update, delete, method calls. This single screenshot is the entire
argument. Show it before anything else.]`

### Primary CTA
**See how the permissions work** *(scroll)* — or **Get it — $199**

### Trust strip
`[PROOF — REPLACE]`
*This audience responds to a different proof than page 1. Best fits, in order:
a named partner or client using it in production; an install count; a security
review or pen-test result; "audited by X". Generic star ratings do less work
here. If you have nothing yet, delete the strip.*

---

## `THE OBJECTION` — state it before they do

**H2: The problem with most AI connectors**

Ask a vendor how their connector authenticates. You usually get one of two
answers.

**"It uses an API key."** There is an account, the account has permissions, and
everything the AI does is that account's doing. The key is almost always scoped
generously, because a narrow key breaks the demo.

**"It runs as a service user."** The same thing, said honestly.

Both share one property: **the AI's permissions have nothing to do with the
permissions of the person using it.** Every access decision your organisation
has already made is bypassed the moment the request leaves the chat window. Your
accounts clerk gets your finance director's visibility, through a chat box,
without anyone changing a setting.

That is the thing to be worried about. It is worth being worried about.

---

## `THE ANSWER` — mechanism, not reassurance

**H2: Four gates. Every request passes all of them.**

Present as a stack or a numbered vertical flow. The narrowest wins.

**1 — What the user consented to**
OAuth 2.1 with PKCE. Each person authorises their own assistant with their own
Odoo login, approving a specific scope. A connection authorised read-only cannot
later write. Revoke any single connection from one screen without touching
anyone else's.

**2 — What you allow**
A governance scope: read-only or read-and-write, an hourly call ceiling, a hard
cap on rows per query. Ships read-only.

**3 — What the AI may touch**
A per-model matrix. Read, create, update, delete and business-method calls, each
switched independently, per model. Plus field blacklists for columns that never
leave the building, and a record filter per model — so "read orders" can mean
"read orders that aren't drafts".

**4 — What their Odoo account permits**
Native access rights and record rules, applied underneath everything above,
because the request genuinely is theirs.

### The property that makes this reviewable

> **Gates 2 and 3 can only subtract.**

Switching something on in the matrix does not grant access — it stops that layer
blocking something the user could already do by hand. **There is no
configuration mistake that gives anyone more access than they started with.**

That is what makes it safe to start permissive and tighten later, rather than
spending two months in a permissions workshop before anyone gets value.

---

## `THE HONEST LIMIT` — the section that earns the sale

**H2: "It can only do what you can do" is necessary. It isn't sufficient.**

Your operations manager can legitimately delete a confirmed order. So an
assistant running as them can delete a confirmed order — correctly, by the
rules, with full authority. It just did it because a language model inferred it
from a sentence.

Running as the user gives you a **ceiling**, and that ceiling is one person's
full authority. For some of your people, that is quite a lot.

Which is why the gate that actually matters is the next one.

---

## `THE APPROVAL GATE`

**H2: The AI drafts. A person commits.**

With approval required, an AI asked to create a purchase order does not create
one. It creates a **pending request** — the operation, the model, the exact
values, and the person who asked — and waits.

A human approves or rejects. On approval it executes **as the original user**, so
their permissions are checked a second time at the moment it actually runs.

`[SCREENSHOT: the approvals queue, showing a pending write with its values
expanded and approve/reject controls.]`

**Why this is the thing that lets you say yes:**

The argument against AI writing to an ERP is rarely "it will be wrong." It is
*"it will be wrong and I won't find out until it matters."*

With a gate, being wrong is cheap — someone reads a queue item and clicks
reject. Without one, being wrong is a phone call to a customer.

---

## `THE AUDIT TRAIL`

**H2: One row per call, with a person's name on it**

Who asked. Which tool. Which model and records. From what IP. How long it took.
An estimated token cost. And whether it succeeded, errored, or was **denied**.

Two things that decide whether an audit trail is real:

- **Every row is attributable to a person.** A connector running as a service
  account writes the same name on every row, which is a list of events, not a
  record of who did what.
- **Refusals are logged too.** A denied call is the more interesting event — it
  means something tried to exceed its scope, which is either a misconfiguration
  or a signal. You want both.

Retention is configurable; old rows purge on a schedule you set.

`[SCREENSHOT: the audit log, filtered to show a mix of ok / error / denied rows
with different users.]`

---

## `WHAT IT CANNOT DO` — concede in full

**H2: Where this doesn't protect you**

Four honest limits. **Do not soften these.** On a governance page, stated
limitations are the strongest trust signal available, and a buyer who has read
three vendor pages of pure upside will stop on this one.

**It can't protect you from a user's own authority.** Grant delete in the matrix,
turn approval off, and an assistant running as someone with delete rights can
delete. Layers are places to say no; they only work if someone says no in one of
them.

**Per-user permissions make some questions unanswerable.** A salesperson asking
about company-wide margin gets their slice, not the company's. That is correct
behaviour and it will still generate support tickets.

**Read-only by default disappoints people.** The first reaction to a read-only
assistant is that it is a toy. It is the right starting position and you will
still spend a week explaining that.

**It is slower to set up than a service account.** An admin key works in five
minutes. Deciding what the assistant may touch takes longer. That decision is
the point, but somebody still has to make it.

---

## `WHAT YOU'RE BUYING` — deployment reality

**H2: An Odoo module, not a second system**

- **Runs inside Odoo.** No separate process to host, monitor, secure or upgrade.
  Deploys with your Odoo, backs up with your Odoo.
- **Nothing beyond what your server already has.** No Node runtime, no
  container, no proxy.
- **Nothing sent to us.** There is no vendor cloud in the path. Your Odoo talks
  to the assistant your team already pays for.
- **Multi-company aware.** Company boundaries apply to AI requests the same way
  they apply in the interface.
- **Standard protocol.** Model Context Protocol over HTTPS, current revision and
  the older handshake-based ones, so you are not locked to one AI vendor.

---

## `FAQ`

**Whose credentials does a request run as?**
The signed-in Odoo user's. There is no service account and no shared token.

**Can we pilot this with one team?**
Yes. Permissions are per user; you can scope one group and leave everyone else
untouched.

**What happens when someone leaves?**
Deactivate their Odoo user and every assistant connected under their name stops
working, immediately. Individual connections can also be revoked without
disabling the account.

**Can we turn the whole thing off?**
Yes — one switch disables the server database-wide, and every connection stops
being answered.

**Where is data processed?**
Inside your Odoo, then sent to whichever assistant your user connected, in
response to their question. Nothing routes through us.

**Does this satisfy our auditor?**
It gives you attributable, per-call logging with configurable retention, and
human approval on changes. Whether that satisfies a specific framework is a
question for your auditor — we will answer technical questions from them
directly.

**What does it cost?**
$199 once, on the Odoo App Store. Unlimited users, no metering, no subscription.

---

## `SECOND CTA`

**H2: Start read-only. Widen when you're ready.**

The safe configuration is the default one — install it, let a team read, and
turn on writing only when you have watched it for a fortnight.

**Get it on the Odoo App Store — $199**
*Or see it running →* `[demo link]`

---

## `CLOSING NOTE` — four questions

A shareable artifact. Someone will screenshot this and take it into a vendor
call, which is exactly what you want.

> **Four questions worth asking any AI/ERP vendor, including us:**
>
> 1. Whose credentials does a request run as? *(If it's a service account,
>    everything else is decoration.)*
> 2. Can I make it read-only, and is that the default?
> 3. Can a write become a request instead of an action?
> 4. Does the log name a person for every call, including refused ones?

---

## Notes for the build

- **The permission matrix screenshot goes above the fold.** On this page the
  proof is the product, not a testimonial. It converts the abstract claim into
  something a technical buyer can evaluate in the ten seconds NN/G says you get.
- **"What it cannot do" must survive review.** There will be pressure to soften
  it. Every softening costs more than the limitation does — this section is why
  a sceptic believes the other six.
- **Different ad creative than page 1.** Page 1 sells the outcome ("ask your
  Odoo anything"). This one sells the absence of risk ("without giving it the
  database"). Same product, opposite emotional entry point; running the same ad
  copy to both wastes the split.
- **Never claim compliance certifications you do not hold.** The auditor FAQ is
  worded to be useful without asserting SOC 2, ISO 27001 or GDPR adequacy. Keep
  it that way unless and until you hold them.
- **No "enterprise-grade", "bank-level security", "military-grade".** This
  audience reads those as red flags. Mechanism beats adjective every time —
  "one row per call, attributable to a person" outperforms "comprehensive audit
  capability".
