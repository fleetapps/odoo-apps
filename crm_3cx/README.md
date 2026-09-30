# 3CX Phone System Integration for Odoo 19 (`crm_3cx`)

Turns a 3CX PBX (V18/V20, **PRO or Enterprise**) and Odoo CRM into one
phone-sales system: every call drives the pipeline, and the pipeline drives
the phone. Over 3CX's CRM Integration API, which is not licence-gated the way
the Call Control and Configuration APIs are — nothing here needs the AI edition.

## What it does for a phone-sales team

**The loop a lead goes through**

1. **A lead arrives** (web form, import, a call or chat from an unknown number,
   a renewal coming up). It gets a *First call* activity and a **speed-to-lead
   SLA** (default 15 min). Leads called within 5 minutes convert many times
   better than leads called the next day; a cron flags the ones nobody dialled
   and tells the team leader.
2. **The agent works *My Calls*** (CRM › Sales): every lead with a call due,
   with a **Call via 3CX** button that opens the 3CX Web Client dialer with the
   number filled in — any browser, no extension — or a `tel:` link for the
   desktop app.
3. **3CX reports the call.** No answer → the **next attempt is scheduled by the
   cadence** (2 h, 1 day, 3 days, 1 week); after N unanswered attempts with no
   conversation the lead is marked lost as *Unreachable*, so the pipeline stays
   honest. First real conversation → the lead **leaves the first stage** on its
   own. Every call is a note in the chatter and a row in the call log.
4. **A customer calls in.** 3CX shows the person, their company, and their
   **open opportunity with its stage** (and *Renewal 02 Oct* when one is
   close); the link opens the deal. With Call Flow Designer, Odoo also tells 3CX
   **which extension owns the deal**, so the IVR can route the caller straight
   to their salesperson.
5. **Missed call** → *Call back* activity for the salesperson (or the agent
   who missed it), closed by the next answered call.
6. **Renewals**: a renewal date on the contact creates a renewal lead 30 days
   ahead (configurable), assigned to their salesperson, into the same SLA and
   cadence. For insurance, that is the retention book.
7. **Managers** get *CRM › Reporting › 3CX Calls* (by agent, day, outcome,
   hour of day, connect rate, talk time) and *Speed to Lead* (how fast leads
   were first dialled against whether they were won), plus stat buttons and a
   *Calls* tab on every lead and contact.

Also: contact search from the 3CX Web Client by name, company, number or
email (contacts and leads), lookup by email for live chat, chat transcripts
in the chatter (SMS, WhatsApp, live chat), lead creation from the 3CX client,
extension ↔ Odoo user mapping, and the 3CX AI summary and recording link on
the call when the PBX provides them.

| When | 3CX does | Odoo does |
|---|---|---|
| A call or SMS/WhatsApp arrives | asks `/api/3cx/crm` who the number is | answers with the contact, company or open deal: names, dialable number, context line, link, owner extension |
| An agent searches in the 3CX Web Client | asks `/api/3cx/search` | returns contacts and leads, up to 20 |
| A call ends | reports it to `/api/3cx/call` with the admin's texts | chatter note, call record, cadence / call-back / stage / SLA rules |
| A chat ends | reports it to `/api/3cx/chat` | transcript in the chatter |
| An agent clicks *Create contact* in 3CX | posts to `/api/3cx/contact` | creates a lead, with First call SLA |
| IVR (Call Flow Designer) asks about a caller | `LookupFromCFD` → `/api/3cx/crm` | raw JSON: `owner_extension`, `customer_status`, `stage`, `renewal_in_days` |

## Why not 3CX's built-in Odoo integration?

3CX V20 Update 10 ships an `OdooCRM` template that drives Odoo's external API
with a user API key. Use it if all you need is caller ID for contacts. This
module exists for what it does not do:

| | 3CX built-in `OdooCRM` | `crm_3cx` |
|---|---|---|
| Matches | contacts and companies | contacts, companies, **leads and opportunities** |
| Number formats | 3CX phonebook setting only | + E.164 / national / stored-format reconciliation on the Odoo side |
| Journaling | note on the contact card | note on the contact **or lead**, plus a call record for reporting |
| Missed calls | note | **Call back activity** for the salesperson (or the agent who missed it), auto-closed |
| Unknown callers | contact created by the agent | lead created by the agent, or automatically |
| Agent identity | none | 3CX extension ↔ Odoo user (user form), fallback on email |
| Chats | note on the contact | note on the contact or lead |
| Credentials on the 3CX side | an Odoo **user API key** (everything that user can do over RPC) | one dedicated key that opens five endpoints |
| 3CX versions | V20 U10+ | V18 and V20 |

A 3CX system runs one CRM integration at a time, so it is one or the other.

Started from the free [`3cxcrm`](https://github.com/crottolo/free_addons)
module by Persevida S.L. / FL1 sro (AGPL-3), which does lookup only and exists
for Odoo 14.0–18.0 (its controller reads `mobile`, removed from `res.partner`
and `crm.lead` in 19.0). Its response keys are kept, so its template still
works for lookup.

## Settings (Settings › 3CX)

| Setting | Default | Effect |
|---|---|---|
| First call within (minutes) | 15 | First call activity + SLA on every new lead with a phone; 0 = off |
| Tell the team leader when overdue | on | note + activity for the team leader on breach |
| Retry cadence (hours) | 2,24,72,168 | delay before the next attempt after an unanswered dial |
| Mark unreachable after (attempts) | 5 | lost reason *Unreachable*; 0 = never |
| Move out of first stage after first conversation | on | |
| Call-back activity for missed calls | on | |
| Lead for unknown numbers | off | inbound calls and chats only |
| Show the caller's open opportunity in the popup | on | also feeds the CFD routing fields |
| Renewal leads (days ahead) | 30 | from *Renewal Date* on the contact; 0 = off |
| 3CX Web Client URL | empty | `https://pbx…` → Call buttons open its dialer; empty → `tel:` links |

## Install

1. Put `crm_3cx` on the addons path, *Apps › Update Apps List*, install
   **3CX Phone System Integration**. A random API key is generated.
2. *Settings › 3CX*: copy the API key, click **Download 3cx_odoo.xml** (it
   comes with this database's URL filled in).
3. On each user's form, *Preferences › 3CX Extension*: the extension they
   answer on. (Without it, the agent is matched on the email 3CX has for the
   extension.) This is also what the IVR routes to.
4. 3CX Admin Console › *Integrations › CRM* › **Add** › upload the file. Enter
   the **API key**; *Odoo URL* is prefilled. Call Journaling, Chat Journaling
   and contact creation are on by default and can be switched off there.
5. **Restart the 3CX System service** (templates load at startup), then use the
   **Test** button with a number that exists in Odoo.

Odoo must be reachable from the 3CX server over HTTPS. Nothing is opened on the
3CX side.

## Number matching

3CX sends the number after its phonebook setting (*Admin Console › System ›
Phonebook › Options*: *Match exactly* or *Match at least N characters*, which
cuts it to the last N digits, no `+`). Odoo then tries, in order: the number as
received, its E.164 form, and its national significant number (parsed with the
company's country), against `phone_mobile_search` on contacts, then on leads —
so `+254712345678`, `0712345678`, `254712345678` and `712345678` all find a
contact stored as `+254 712 345678`. People rank before their company. The
searched number is echoed back as a phone output because 3CX discards a match
whose phones do not include it. Fewer than five digits is not searched.

## Endpoints

All `POST`, JSON body, header `apikey: <key>`; wrong key → `403`.

```bash
curl -sS -X POST https://your-odoo/api/3cx/crm -H 'Content-Type: application/json' -H 'apikey: KEY' \
  -d '{"number": "+254712345678"}'
# {"result": [{"partner_id": "12", "entity_type": "Contacts", "firstname": "Wanjiru", "lastname": "Kamau",
#              "name": "Mavuno Traders", "email": "...", "phone": "+254 712 345678", "phone_e164": "+254712345678",
#              "number": "+254712345678", "web_url": "https://your-odoo/odoo/contacts/12", ...}]}
# {"result": [], "new_number": true}                        when nothing matches
# leads come back as "partner_id": "L34", "entity_type": "Leads"

curl -sS -X POST https://your-odoo/api/3cx/search -H 'Content-Type: application/json' -H 'apikey: KEY' \
  -d '{"field": "name", "text": "wanjiru"}'                 # name | company | phone | email (+ "exact": true)

curl -sS -X POST https://your-odoo/api/3cx/call -H 'Content-Type: application/json' -H 'apikey: KEY' \
  -d '{"number": "+254712345678", "call_type": "Missed", "entity_id": "12", "agent": "101",
       "agent_first_name": "Jane", "agent_last_name": "Doe", "agent_email": "jane@example.com",
       "queue": "800", "duration": "00:00:00", "started_at": "2026-09-19T08:30:00Z",
       "subject": "3CX call", "body": "19/09/2026 11:30: Missed call from +254712345678 ..."}'
# {"logged": true, "model": "res.partner", "id": 12, "created": false, "call_id": 7, "activity_id": 3}
```

`call_type` is 3CX's `Inbound`, `Missed`, `Outbound` or `Notanswered`. `body`
and `subject` are the texts the 3CX administrator configured (rendered by 3CX);
without them Odoo writes its own line. `recording_url`, `summary` and
`transcription` are added when the PBX provides them. The note's author is the
Odoo user behind the extension, else OdooBot.

## Tests

`odoo -d <db> -i crm_3cx --test-enable --test-tags /crm_3cx --stop-after-init`

## Roadmap (needs 3CX's Call Control API, 8SC+ AI licence)

Click-to-dial from Odoo in any browser (`POST /callcontrol/{dn}/devices/{id}/makecall`)
and a screen pop inside Odoo when the mapped user's extension rings (WebSocket
`/callcontrol/ws` → `res.partner._bus_send`). Both degrade to nothing without
that licence; everything above works without it.

## License

AGPL-3.0-or-later. Original `3cxcrm` © Persevida S.L. / FL1 sro; this module
© 2026 Odin. Source: https://github.com/fleetapps/odoo-apps
