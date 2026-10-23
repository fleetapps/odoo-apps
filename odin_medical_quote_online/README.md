# Medical Quotes Online

A public page, **`/medical-quote`**, where anyone can price medical cover for
their family without knowing any insurance words, keep their quote, and ask
to be called. Each quote lands in CRM for an agent. No Website app is needed.

## The visitor

1. **Who needs cover?** Just me / me and my partner / me and my children /
   my whole family / only my children. Then ages, not dates of birth.
2. **Plans:**
   * The plans that fit, cheapest first, priced in full (levies and stamp
     duty included), with a monthly option.
   * *Also cover* chips: doctor visits & medicines, dental, eyes & glasses,
     pregnancy & birth.
   * *Hospital cover a year* bands.
   * A benefit a plan only sells with another one (dental with outpatient)
     is added and explained.
   * Up to 3 plans go on the quote.
3. **Where should we send your quote?** Name, phone, optional email, WhatsApp
   or call or email, and consent to be contacted.
4. **Their own quote page:**
   * the plans in plain words;
   * Download PDF (the medical quotation);
   * Chat with us on WhatsApp;
   * Change my answers;
   * **Talk to an advisor**: how and when to be contacted.

Riders (Hosi Cash, Femina...) are not offered online. The advisor adds them.

## The office

* **Opportunity:** *Online quote – Name (N people)*.
  * Tag *Online quote*.
  * Source from the link (`utm_source=WhatsApp`), else *Online medical
    quote*.
  * Assigned, in this order:
    1. the agent whose link was used;
    2. **Settings ▸ Sales ▸ Online medical quotes ▸ Who follows them up**;
    3. the team leader.
  * A to-do due today, given by OdooBot, so the agent is notified.
  * A note with the contact preference, the consent time, and any contact
    already on file with the same phone or email.
* **Talk to an advisor** replaces that to-do with a *Call back* due today and
  marks the opportunity high priority.
* **Quotes register:** *Online* and *Asked for a call* filters, and a Channel
  column.
* **Edit quote** opens it in Add Medical Quote, with a reminder to confirm the
  dates of birth.
* **Send online quote link** on a lead or opportunity sends a link only for
  that client.
  * Their name and number are filled in, and the quote comes back to that
    opportunity.
  * It is posted in their WhatsApp chat (whatsapp_connector), or opens
    wa.me.
* **Cover Quotes ▸ Share online quote page** gives an agent their own link,
  for anyone, a group or a status.

## Safety

* **A visitor never sees existing data.** An online quote always makes a new
  contact, and the office is told about possible duplicates. Only an agent's
  link, signed with the database secret, attaches to an existing
  opportunity.
* **Quote pages** need their random token.
* **Every price is worked out again on the server** at saving.
* **Spam:**
  * a honeypot field;
  * at most 5 quotes per phone a day (`odin_medical_quote_online.max_per_phone_day`);
  * at most 100 an hour (`odin_medical_quote_online.max_per_hour`).

## Setting up

* **Settings ▸ Sales ▸ Online medical quotes:**
  * who follows them up and the sales team;
  * the WhatsApp number (defaults to the company phone);
  * the reply time;
  * the privacy policy link.
* **The company logo** for the page header.
* **`web.base.url`** set to the public address, since links are built from
  it.
* **On a quotation template, untick *Show in online quotes*** for plans that
  only agents should quote.
* An outgoing mail server, for notifications and the emailed PDF.

## Not done yet (MVP)

* Children's ages: every child is priced in the child band.
* Insurer logos on the cards.
* Translations.
* reCAPTCHA.
