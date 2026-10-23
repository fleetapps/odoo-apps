# Medical Quotes Online

A public page, **`/medical-quote`**, where anyone can price medical cover for
their family without knowing any insurance words, keep their quote, and ask
to be called. Each quote lands in CRM. No Website app is needed.
`odin_motor_quote_online` adds the same for motor (`/motor-quote`) and
reuses everything below.

## The visitor

1. **Who needs cover?**
   * Just me / me and my partner / me and my children / my whole family /
     only my children.
   * Then their ages, not dates of birth.
2. **Plans:**
   * The plans that fit, cheapest first, priced in full (levies and stamp
     duty included), with the monthly option. The cheapest is marked
     *Lowest price*.
   * A summary card, *Prices for you (36), your partner (34), 2 children*,
     with **Edit**. Finished steps at the top go back too.
   * **Add to your cover**: *Every plan pays for hospital stays. Tap
     anything else you want covered.* The choices are doctor visits &
     medicines, dental, eyes & glasses, and pregnancy & birth.
   * **How much hospital cover?**: any amount, up to 300K, 300K to 1M, or
     over 1M.
   * A benefit a plan only sells with another is added, marked *Added:
     this plan needs it for Dental care*.
   * Up to 3 plans go on the quote.
3. **Where should we send your quote?**
   * Name, phone and an optional email.
   * WhatsApp, call or email.
   * Consent to be contacted.
4. **Their own quote page:**
   * the plans in plain words;
   * Download PDF;
   * Chat with us on WhatsApp;
   * Change my answers;
   * **Ask an advisor to contact me**: how and when.

Riders (Hosi Cash, Femina...) are not offered online. The advisor adds them.

## Who follows it up

| The client came by | It goes to |
|---|---|
| a link sent from their opportunity (**Send quote link** on a lead) | that opportunity and its salesperson |
| an agent's own link | that agent |
| an ad, the website, a campaign link or the plain page | whoever already has an open opportunity with that phone or email; else the person in **Settings ▸ Online quotes**; else **nobody: the team pool** |

* **The team pool.** The opportunity has no salesperson. It is in the
  online quotes team (or the default team). The team's members are told in
  their inbox, or the sales managers if the team has no members.
  * Anyone can take it with **Assign to me**, on the quote or on the
    opportunity. A second person gets *Quote Agent has already picked this
    up*.
  * Setting the Salesperson in CRM, or CRM's own assignment rules, does the
    same.
  * Either way the quote, its quotations and the *contact the client* to-do
    become theirs. If the client already asked to be called, the to-do is
    *Call back*.
* **An agent or an assigned person** gets a to-do due today, given by
  OdooBot so they are notified.
* **The opportunity:**
  * named *Online medical quote – Name (N people)*;
  * tagged *Online quote* and *Medical*;
  * its source and campaign come from the link (`utm_source`,
    `utm_campaign`), else *Online medical quote*;
  * a note gives the contact preference, the consent time, and any contact
    or open opportunity with the same phone or email.
* **Talk to an advisor** stars the opportunity and replaces the to-do with a
  *Call back* due today. While nobody has picked it up, the team is told
  again.
* The visitor never learns who has their number: the page names the agent
  only to a client whose agent quoted them or sent the link.

## The office

* **Cover Quotes ▸ Online quotes**: what clients made themselves, newest
  first. Unclaimed quotes are in bold with **Assign to me** on the row, and
  Source and *Asked for a call* are shown.
* **Quotes** has every quote with a **Channel** column (Sales team or
  Online), and these filters:
  * *Sales team*, *Online*, *To pick up* and *Asked for a call*;
  * group by Channel, Source or Campaign.
* Each online quote says what it **Came through**: an agent's link, a link
  sent to the client, or an ad, the website or a shared page.
* **Cover Quotes ▸ Share quote link**, Medical or Motor:
  * **Anyone I send it to**: your own link, for a chat, a group or your
    status. Quotes come to you.
  * **An ad, the website or a campaign**: pick where it runs (Facebook,
    Instagram...) and optionally a campaign. The link carries those and
    nobody's name, so its quotes go by the table above.
  * From a lead, **Send quote link**: a link for that client only, their
    name and number filled in. Motor for a lead tagged Motor.
  * It is posted in their WhatsApp chat (whatsapp_connector), or opens
    wa.me.
* **Share on WhatsApp** on any quote made on the quote screen links to the
  client's own page rather than a bare PDF.

## Links that already know something

* `/medical-quote?who=family` (or `me`, `couple`, `me_kids`, `kids`) opens
  with who needs cover chosen.
* `/motor-quote?class_id=…&cover=tpo&period=1` opens with the vehicle type,
  cover and period chosen.
* Anything not offered is ignored, and the rest of the questions are still
  asked.
* `odin_wa_autoreply` builds these links from what a customer wrote on
  WhatsApp.

## Safety

* **A visitor never sees existing data.** An online quote always makes a new
  contact, and the office is told about possible duplicates. Only a link
  sent from an opportunity, signed with the database secret, attaches to
  that opportunity.
* **Quote pages** need their random token.
* **Every price is worked out again on the server** at saving.
* **Spam:**
  * a honeypot field;
  * at most 5 quotes per phone a day (`odin_medical_quote_online.max_per_phone_day`);
  * at most 100 an hour (`odin_medical_quote_online.max_per_hour`).

## Setting up

* **Settings ▸ Sales ▸ Online quotes:**
  * both page links;
  * who follows quotes from ads up: a person, or empty for the team pool;
  * the online quotes sales team, whose members are told;
  * the WhatsApp number (defaults to the company phone);
  * the reply time;
  * the privacy policy link.
* **The company logo** for the page header.
* **`web.base.url`** set to the public address, since links are built from
  it.
* **On a quotation template, untick *Show in online quotes*** for plans that
  only agents should quote.
* An outgoing mail server, for notifications and the emailed PDF.

## Not done yet

* Children's ages: every child is priced in the child band.
* Insurer logos on the cards.
* Translations.
* reCAPTCHA.
