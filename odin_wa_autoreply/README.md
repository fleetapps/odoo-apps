# WhatsApp Auto-reply

When a customer writes on WhatsApp and nobody answers, they get a reply
anyway, and the team is chased. It is built on `whatsapp_connector` and on
the online quote pages (`odin_medical_quote_online`, and
`odin_motor_quote_online` when installed).

## The plan

### 1. Every message can be answered

* **Sending to business-scoped user IDs** is on, on the WhatsApp account.
  Customers who reach the business without sharing a phone number can then
  be answered.
* **Somebody must be notified.** Set the account's **Notify users**, or turn
  on Lead Routing. Without them, a new conversation has no members and
  nobody hears about it. This is a setting, not code.

### 2. Timing

* **Outside business hours**, the reply goes out at once.
* **During business hours**, the agents have 10 minutes, then it goes out.
* Business hours are Monday to Friday 8am to 5pm and Saturday 9am to 1pm, in
  Africa/Nairobi time. They can be changed on the account.

### 3. What the customer gets

The customer's unanswered messages are read for keywords, in English and in
Swahili and Sheng:

| The message is about | Reply |
|---|---|
| medical (health, hospital, family, *afya*, *matibabu*...) | the medical quote link |
| motor (car, third party, pick-up, matatu, boda, *gari*, a make, a number plate...) | the motor quote link |
| nothing clear ("Hi", a voice note, a photo) | both links, and "or just tell us what you need" |

The link opens already set up where the message says enough. "Family" sets
who needs cover. "Third party for one month" sets the cover and the period.
"Pick-up" sets the vehicle type.

### 4. The link is the customer's own

* The conversation gets a CRM lead: the customer's open lead, or a new
  *WhatsApp — Name* lead.
* The link is signed for that lead, so the page already knows the customer's
  name and phone.
* The quote they make goes on that lead and contact, with no duplicate.
* It goes to the conversation's owner if there is one, otherwise to the
  online quotes pool (Assign to me).

### 5. When not to send a quote link

* **Service**: payments (M-PESA confirmations), KRA PIN, claims, accidents,
  certificates, stickers, refunds, complaints.
  * The customer is told: "We've received your message and an advisor is
    attending to it."
  * The escalation people get a to-do at once.
* **Not something the page can price**:
  * other products (personal accident, travel, life, home, business, group,
    fleet...);
  * medical cover for parents or siblings.
  * The customer is told: "An advisor will reply here shortly", or "from 8am
    tomorrow".
* **Existing clients**: someone with a won opportunity, a confirmed order or
  an accepted quote, or an open opportunity with a salesperson.
  * The customer is told their agent will get back to them.
  * The agent gets a to-do.
* **Never**:
  * to staff;
  * to anyone who replied STOP;
  * when an agent has replied in the last 24 hours (a person is already
    talking to them);
  * more than once a day per conversation.

### 6. Escalation while the customer waits

* **After 2 working hours** without an agent's reply, the team is told:
  the conversation's owner, else the Notify users, else the online quotes
  team, else the sales managers.
* **4 hours before WhatsApp's 24-hour window closes**, the escalation person
  gets a to-do: "Reply to Jane before 9:44pm". After that, only templates can
  be sent, so they should send *Pick up the conversation*.
* An agent's reply, or a template they send, ends the wait.

## Settings

**WhatsApp ▸ Configuration ▸ Accounts ▸ (account) ▸ Auto-reply** has:
* the auto-reply on or off;
* the minutes to wait during business hours;
* business hours and time zone;
* when to tell the team;
* how long before the window closes to escalate;
* who to escalate to (empty: WhatsApp managers, else sales managers).

## How it works

* Each incoming message marks the conversation as waiting and schedules the
  reply. An agent's reply (from Discuss, or a template) clears it.
* A scheduled action every 5 minutes, also woken at each reply's due time,
  sends the replies that are due and the alerts.
* Auto-replies are sent by OdooBot, marked *Auto-reply* on the WhatsApp
  message. They do not count as an agent's reply, so listeners are not muted
  and the escalation still runs.
