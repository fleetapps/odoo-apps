# Article 3 — product craft / differentiation

**Target:** Odoo partners, integration developers, product people. Substack,
Medium, Odoo community forums, LinkedIn.
**Job:** differentiate on care. A feature list cannot show that you sweat the
first five minutes; a post-mortem can.
**Length:** ~1,600 words.
**Tweet test:** "Our integration installed cleanly, connected successfully, and
could not answer a single useful question. Five failures, none of which raised
an error." — the tweet works; the five failures are the article.

---

## Headline

# The integration installed fine. It just couldn't answer a single question.

**Alternates:**
- Five ways an integration fails after the install succeeds
- Green build, green install, dead product

## Subtitle / standfirst

The install passed. The connection handshake passed. Every test passed. Then the
first real question came back empty — and so did the next four. A post-mortem on
the gap between shipping software and shipping something usable.

---

## Opening (the hook)

We shipped an Odoo app that connects AI assistants to your ERP. Install
succeeded. OAuth handshake succeeded. The assistant reported itself connected,
and the connection was genuinely live.

Then someone asked it what last month's sales were, and it said it had no access
to sales orders.

They asked what was in stock. No access to stock.

They asked what it *could* see, and it answered, accurately: contacts,
companies, countries and currencies.

Nothing had failed. There was no error to look up, no exception in the log, no
red anywhere. We had built a working connector to a system full of business data
that could describe the currency table.

Here are the five failures behind that, in the order we found them. None of them
raised an error. Every one was invisible from inside the thing that was broken.

## Failure 1: The permissions we shipped were the permissions nothing could use

A new install seeded a read-only scope covering four models: contacts,
companies, countries, currencies. All four belong to the framework, not to any
business app.

This was not laziness. It was a constraint we had reasoned ourselves into.

Odoo data files are declarative and they resolve their references at install
time. A data file that names `sale.order` **fails the install** on any database
without the Sales app. Not gracefully — the module refuses to install. So the
safe set was the set present in every database, which is the framework's own
tables, which is nothing anyone asks questions about.

We had correctly identified a real constraint and then accepted its worst
consequence as fixed.

The way out was to stop declaring and start resolving. A post-install hook runs
Python, and in Python a missing model is an empty result rather than a fatal
error. So the hook walks a list of models a business actually asks about —
sales, purchasing, invoicing, stock, CRM, projects, employees — looks each one
up at run time, and adds the ones this database happens to have. Absent apps are
skipped in silence.

The general shape, which took us far too long to see: **when a declarative
format forces you into a bad default, the answer is usually to move the decision
to run time, not to accept the default.**

## Failure 2: Nobody could find the app

The module defined a permission group. Menus were gated on it, correctly. Record
rules keyed off it, correctly.

Nothing granted it.

Administrators had it, because administrators inherit everything. So it worked
perfectly for every person who tested it, and was invisible to every other
employee in the company. The app was installed. It did not appear.

Nobody reported this, which is the interesting part. **Nobody files a bug about
a menu they have never seen.** The failure had no symptom, because its symptom
was absence.

The fix was two lines, granting the role to every internal user. What took the
time was the argument, and the argument is worth repeating: was this a security
decision or a navigation decision?

It is navigation. The three gates that matter — the read-only default scope, the
per-model permission matrix, and the user's own access rights underneath both —
were all untouched. Someone reaching the menu can connect an assistant that sees
exactly what they can already see. Withholding the *menu* was never protecting
anything. It was just hiding the product from its users.

The rule we wrote down: **if a group only controls visibility, granting it is a
UX decision, and hiding behind "it's a permission" is how a feature ships to
nobody.**

## Failure 3: The URL was right and kept becoming wrong

The screen showed the server address to paste into your assistant. Behind a
TLS-terminating proxy it showed `http://`, and every AI client refuses a plain
http endpoint — with an error that never mentions the scheme, so the user has no
idea what went wrong.

Two causes, compounding, and the second is the nasty one.

**Odoo only applies proxy header handling when `proxy_mode` is on *and* the
proxy sends `X-Forwarded-Host`.** Front ends that send `X-Forwarded-Proto`
without it — Cloudflare among them — leave the scheme as http no matter how you
configure it.

**And Odoo rewrites its own recorded base URL to wherever an administrator last
signed in from**, unless a second parameter is set to freeze it. So on such a
deployment, an administrator fixing the address by hand would watch it revert on
their next login. Working connector on Monday, silently broken by Wednesday, no
change anyone made.

That second one is the kind of bug that destroys trust in a product, because the
user's mental model — "I fixed it, it's fixed" — is correct everywhere except
here.

We now derive the public address from the forwarded headers directly, never
downgrade https once anything has reported it, and offer a one-click fix that
pins and freezes the value. The manual route existed the whole time. It required
knowing that developer mode, System Parameters, and a second undocumented
parameter all existed. That is not a fix; it is a scavenger hunt.

## Failure 4: We suggested things we would then refuse

The screen offered starter prompts — click to copy, paste into your assistant.
"List today's sales orders over $1,900." "Search for unpaid invoices."

On a fresh install, per failure 1, none of those models were readable. So the
first thing a new user did was click a suggestion, hand it to their assistant,
and get told access was denied.

The chip was ours. The refusal was ours. To the user it was one experience, and
the experience was *this product is broken*.

Suggestions are a promise. If your UI proposes an action, the action has to
work — otherwise the suggestion is worse than the blank screen it replaced,
because a blank screen does not make a claim.

Prompts are now filtered twice: against the permission matrix, and against the
user's own access rights. A warehouse user is not offered a finance question
they would be refused. Two prompts that work against any configuration always
lead, so the list is never empty.

## Failure 5: Nothing proved anything

This one is not a bug. It is the failure the other four grew in.

Our setup screen ran a careful checklist. Public address: correct. Reachable
from the internet: yes. Database selector off. Permissions configured. Server
enabled. Every row green.

Every row was a **precondition**. Not one of them was **evidence**. You could
pass the entire checklist and still be unable to answer a question — which is
precisely what happened, five times over, while the screen showed green.

So we added one button: *Run a test question as me.*

It picks a model the user can actually read, and issues a real tool call through
the real engine — every permission gate, the real audit trail, running as them —
then shows the rows that came back. It is not a mock and not a ping. It is the
same code path a connected assistant uses.

It found failure 1 immediately, in a way that five green checkmarks had not.

The lesson generalises past our product: **a checklist of preconditions is not a
test.** If your setup flow cannot demonstrate the thing working end-to-end, it
is asking the user to be the integration test — and they will run it in their
assistant, where the failure looks like your product being broken rather than
your product being unconfigured.

## What connects all five

Every one was invisible from inside the system that had the problem.

- The scope could not know it was thin, because four models is a valid scope.
- The menu could not know nobody saw it, because it rendered correctly for
  everyone who looked.
- The URL was correct at the moment we generated it.
- The prompts were valid strings.
- The checklist was accurate about everything it checked.

Each component was locally right. The product was globally useless. And no
integration test we could have written would have caught it, because every test
we could write ran with an administrator, on a database we controlled, in an
environment we set up — which is to say, as the one user for whom none of these
failures existed.

The only thing that finds this class of bug is asking, in the user's position,
with the user's permissions: **can it actually do the thing yet?**

That question is now a button on the first screen.

## Closing

There is a version of this post that is a list of bugs, and it would be less
useful. The point is not that we had five failures. It is that "installed
successfully" and "usable" turned out to be separated by five distinct problems,
none of which any conventional signal — build, test, install, connect — could
detect.

If you ship integrations, the gap is worth measuring in your own product. Install
it as a real user, on a database you did not prepare, and ask it to do the first
thing your documentation promises.

Our gap was five failures wide. I do not think that is unusual. I think it is
just unusually measured.

---

*Written while shipping [AI MCP](link), which connects Claude, ChatGPT, Cursor
and any MCP client to Odoo. Every failure above is real and every fix above is
in the current release.*

---

## Editorial notes

- **The trifecta:** opening scene (the assistant describing the currency table)
  → the peak at failure 5, where the reader realises the checklist itself was
  the flaw → closing that reframes it as a measurement problem rather than a
  bug list.
- **Failure 5 must stay last.** It is the only one that is not a bug, and it is
  the one a reader will remember and repeat. Peak-end rule.
- **"What connects all five" is the section that makes this an article rather
  than a changelog.** If you cut for length, cut *within* failures 2–4, never
  this.
- **The self-deprecation is doing real work.** "We had correctly identified a
  real constraint and then accepted its worst consequence as fixed" is the line
  that makes a partner trust the rest. Do not sand it down.
- **No numbers to verify** in this piece by design — it is narrative, so there
  is nothing here that needs a citation or that a reader can catch you
  overstating.
