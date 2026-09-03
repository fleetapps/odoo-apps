# Article 1 — technical / credibility

**Target:** engineers building or evaluating MCP servers. Hacker News, dev
Substacks, r/programming, LinkedIn technical audience.
**Job:** earn credibility on merit. Give away a genuinely useful insight. The
product appears once, at the end, as a footnote.
**Length:** ~1,500 words.
**Tweet test:** "Your MCP server caps results at 200 rows. Your model has no way
to know that, so it reports a partial page as the whole answer. Fetch limit+1
and return has_more." — survives as a tweet, but the *why* is the article.

---

## Headline

# Your MCP server caps results at 200 rows. Your AI doesn't know that.

**Alternates:**
- Row caps make an AI confidently wrong, and the fix is three fields
- The most dangerous thing an MCP tool can do is succeed

## Subtitle / standfirst

A row cap is a good idea that quietly turns a correct database into a
confidently wrong answer. Here is why it happens, why it is worse than an error,
and the three-line fix that belongs in every MCP server.

---

## Opening (the hook)

> Someone asks their assistant: *how many overdue invoices do we have?*
>
> It runs a search against the ERP, gets 200 rows back, counts them, and
> answers: **"You have 200 overdue invoices, totalling $412,000."**
>
> The real number is 1,847, totalling just under four million.

Nothing failed. No exception was raised, nothing was logged, no error reached
the user. The database was right, the server was right, and the model did
exactly what the data told it to do.

That is the problem.

## Section: Where the 200 comes from

Any MCP server that exposes a search tool over a real database needs a row cap.
Without one, a model that decides to "have a look at the orders table" can pull
half a million rows into a context window, and take the database down on the
way. So you write something like this, and you are right to:

```python
limit = min(requested_limit or 200, scope.max_records)
records = env[model].search_read(domain, fields, limit=limit)
return {"model": model, "count": len(records), "records": records}
```

The cap is correct. The bug is in the last line.

`count` is the length of the page. On any result set larger than the cap, it
equals the cap — so the reply says `count: 200` whether the true answer is 200,
201, or two hundred thousand. The response contains no signal, anywhere, that
it has been truncated.

## Section: Why this is worse than an error

An MCP client handles a tool error well. `isError: true` with a readable message
is something a model can reason about: it will apologise, narrow the query, ask
you a clarifying question. Errors are recoverable, and the whole tool-calling
loop is designed around them.

A truncated success is not recoverable, because nothing about it looks wrong.

Consider what the model has to work with. It sees a well-formed response with a
plausible number of well-formed rows and a field explicitly labelled `count`. It
has no access to your source, no visibility of your configuration, and no memory
of what your cap is. Every heuristic available to it says the query succeeded.

So it does the reasonable thing and reports the answer. The failure surfaces
weeks later, in a meeting, when someone notices the number was wrong — and by
then nobody remembers which question produced it.

This is the specific failure mode that makes people distrust AI over business
data, and it is almost always the server's fault rather than the model's.

## Section: The fix

Fetch one row past the cap. Return the cap's worth. Say what you did.

```python
limit = self._clamp_limit(scope, args.get("limit"))
records = env[model].search_read(domain, fields, limit=limit + 1, offset=offset)

has_more = len(records) > limit
records = records[:limit]

return {
    "model": model,
    "count": len(records),   # rows in THIS page
    "limit": limit,          # the cap that applied
    "offset": offset,
    "has_more": has_more,    # is there anything past it
    "records": records,
}
```

The extra row is never returned. It exists only to answer the question "was
there more?" — one row of cost for the difference between an answer and a
guess.

Then say it again where the model will actually read it, in the tool
description:

> The reply carries `count`, `limit` and `has_more`. When `has_more` is true
> there are further matching records beyond this page — page with `offset`,
> narrow the domain, or aggregate instead, and never present a capped page as
> the complete answer.

That sentence does more work than the schema does. The model reads tool
descriptions before it decides how to use a tool; it reads your field names
only after it already has the data.

## Section: The same bug, four more times

Once you see the shape of it, it is everywhere. **Every boundary your server
enforces must be visible in the response it returns.** A boundary the caller
cannot see is a boundary the caller will misreport.

Audit your own server for these:

**Aggregations.** A capped `read_group` is worse than a capped search, because
the result still totals up. Revenue by month with the last four months silently
missing looks exactly like revenue by month.

**Name lookups.** Truncate a name search and the model picks the first "Acme"
it sees — then writes to it. A cap on a search is a reporting bug; a cap on an
entity resolution is a data-integrity bug.

**Permission filtering.** If your row-level security drops records before they
reach the response, the model reports what it can see as what exists. Sometimes
you cannot disclose the difference. But you can usually say *that* there is a
difference.

**Archived and soft-deleted rows.** If your permission layer hides them at call
time but your `list_models`-equivalent still advertises them, you are promising
access that every subsequent call refuses.

Each one has the same fix: return the boundary alongside the data.

## Section: The underlying principle

Tool design for language models is API design under two constraints that do not
apply to normal APIs.

**Your caller cannot read the docs at runtime.** A human integrator reads your
documentation, discovers the cap, and writes pagination. A model gets one
in-context description and whatever your response contains. Anything not in
those two places does not exist.

**Your caller cannot see your logs.** When a human integration behaves oddly,
someone opens a dashboard. When a model's tool call is silently degraded, the
degradation is invisible on both sides — the model does not know, and the user
sees a confident sentence.

Which produces a rule worth writing on the wall:

> **A tool result should be interpretable by someone who can see only the tool
> result.**

Not your source. Not your config. Not your logs. If a fact is required to read
the response correctly, that fact belongs in the response.

## Section: Four questions for your own server

Run these against whatever you have shipped:

1. Can a caller tell a full page from a complete result set?
2. Does every cap, filter and default that shaped the response appear in the
   response?
3. Does the tool description say what to do when a limit is hit — not just that
   one exists?
4. If you deleted every log line and every doc page, could a reader still
   interpret the payload correctly?

If the answer to any of them is no, you have this bug. It is not exotic and it
is not rare. It is the default behaviour of the most obvious way to write the
code.

## Closing

The uncomfortable part is that a row cap is *good engineering*. It protects the
database, it bounds the context window, it is exactly what a review would ask
for. It became a correctness bug only because the response never mentioned it.

That is the thing to take away, and it generalises well past MCP: **a safety
mechanism the caller cannot observe is indistinguishable from a lie.** Not
because anyone lied — because the truthful part was never sent.

---

*I maintain an MCP server for Odoo, which had exactly this bug until recently.
The fix above is what shipped. It is three lines, and if you run an MCP server
over anything with more rows than a context window, it is probably three lines
you need too.*

---

## Editorial notes

- **The withheld insight** (peak-end rule) is the "four more times" section —
  the reader arrives thinking this is one bug and leaves knowing it is a class.
  Do not move it earlier.
- **The product mention is one italic paragraph at the very end**, after the
  value has been delivered in full. If you move it up, this piece stops working
  as credibility and starts reading as content marketing, and the audience for
  it is unusually good at spotting the difference.
- **Code blocks are load-bearing.** NN/G's scanning research applies: an
  engineer scans to the code first and reads the prose only if the code looks
  competent. Keep both blocks short enough to read without scrolling.
- **Do not add statistics you cannot source.** The 200 / 1,847 / $412,000 in the
  opening are illustrative of a scenario and read as such. Do not convert them
  into a claim about a real customer.
