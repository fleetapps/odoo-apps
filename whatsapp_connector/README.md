# WhatsApp Connector for Odoo 19 (`whatsapp_connector`)

One company WhatsApp Business number for many Odoo users, on Odoo 19
Community. It connects a number on Meta's WhatsApp Business Platform (Cloud
API) and works in one of two ways, chosen per number:

* **No Lead Routing**: behaves like Odoo Enterprise's WhatsApp app. A
  customer's message opens a Discuss conversation with the number's Notify
  users.
* **Lead Routing**: each new conversation becomes a CRM lead owned by one
  salesperson, picked by round-robin, on the same company number.

The full behaviour, with the reasons and sources for each rule, is in
[SPEC.md](SPEC.md).

## What it does

### No Lead Routing (Enterprise-style)

* A customer's first message opens a **WhatsApp** conversation in Discuss with
  all of the number's Notify users. It shows under its own WhatsApp heading in
  the Discuss sidebar, with the customer's picture, number and the time left
  in Meta's 24-hour window.
* Once one user replies, the other Notify users stop being notified about that
  conversation. After 15 days without a reply from a user, they are all
  notified again.
* Replies typed in Discuss go out through WhatsApp while the customer's 24-hour
  window is open. After that the composer is locked and offers **Send
  Template**.
* Each sent message shows its status (sent, delivered, read). A failed message
  shows why and has a **Retry** button.
* **Create Lead** (Discuss header and the conversation's form) finds the
  customer's lead or creates one, and links it to the conversation.

### Lead Routing

* A new customer goes to the salesperson of their open lead, if they have one.
  Otherwise the conversation goes round-robin to the next Notify user with
  *Routing Enabled*. If nobody is available, it goes to the *Fallback User*.
  Without one, it waits unassigned and WhatsApp managers are notified.
* A lead is found or created (source **WhatsApp**, Click-to-WhatsApp ad data
  kept) and linked. The conversation's owner and the lead's salesperson are
  always the same person: changing either one moves the other. This includes
  CRM's own lead assignment and lead merges.
* Only the owner is a member of the conversation and gets its notifications.
  Only WhatsApp managers can invite others or reassign it.
* Salespeople in the number's routing see unassigned conversations and can
  **Take** them. Managers can **Assign to Me**, or change the owner on the form.
* Closing keeps the history. The customer's next message reopens the
  conversation with the same owner while that owner is still eligible, and
  with a new lead if the old one was won or lost.
* Every assignment is written in the conversation, naming the previous owner.

### Templates and sending from records

* **Sync Templates** imports the number's templates from Meta. Templates can
  also be written in Odoo and sent to Meta for approval.
* Template variables are filled from the record (a field or its portal link),
  with the sender's name, or typed when sending.
* The **WhatsApp** button in the chatter opens the *Send WhatsApp Message*
  wizard, with a preview, on any record whose model has approved templates.
* The **Send WhatsApp** server action type sends a template from automations.
* Leads and contacts get a WhatsApp smart button listing their conversations.

## Do not install with Odoo Enterprise's WhatsApp app

Both use the same Discuss channel and message types. The manifest excludes
`whatsapp`, so Odoo refuses to install one while the other is installed.

## Install

1. The server needs the `phonenumbers` Python package (it is in this
   repository's `requirements.txt`). The install script below prints `python
   dependency present: phonenumbers` when it is there.
2. On the instance, as root:

   ```
   curl -fsSL https://raw.githubusercontent.com/fleetapps/odoo-apps/19.0/tools/odin-install-module.sh | bash -s whatsapp_connector
   ```

   Then *Apps › Update Apps List* and install **WhatsApp Connector**.
3. Incoming messages, sending and media downloads run as scheduled actions, so
   Odoo must run with `max_cron_threads` above 0.
4. Odoo must be reachable from the internet over HTTPS: Meta posts every
   incoming message to it.

## Connect the number

*WhatsApp › Configuration › WhatsApp Business Accounts › New*:

1. **Connection**: WABA ID, Phone Number ID, App ID, App Secret and Access
   Token. Use a permanent **System User** token with the permissions
   `business_management`, `whatsapp_business_messaging`,
   `whatsapp_business_management` and `whatsapp_business_manage_events`.
2. **Receiving Messages**: copy the *Callback URL* and *Verify Token* into
   the Meta app's WhatsApp webhook configuration. Subscribe these webhook
   fields:
   * `messages`
   * `message_template_status_update`
   * `message_template_quality_update`
   * `template_category_update`
   * `account_update`
   * `user_id_update`
3. **Subscribe App**, then **Test Connection**. It checks the token, the
   number's status and the app subscription. The Meta app must also be
   **Live**: in Development mode Meta only delivers to test numbers.
4. **Notify users**: the users who handle this number's conversations.
5. **Sync Templates**.

## Who sees what

Set on each user's form, under *WhatsApp*:

| Group | Can |
|---|---|
| User | Work in the conversations they are a member of; send messages and templates. In Lead Routing, also see and take the number's unassigned conversations when *Routing Enabled*. |
| Manager | See every WhatsApp conversation, assign and reassign, manage numbers, Notify users, routing and templates; read the Message Log. |
| Administrator | Also the Meta credentials, the webhook settings and the Webhook Log. |

## Lead Routing settings

On the number's form, *Conversation Routing*:

* **Lead Routing**: switches the number to Lead Routing. Conversations that
  already exist keep working the way they started.
* **Method**: Round Robin.
* **Fallback User**: gets new conversations when no Notify user is available.
* **Last Assigned**: where round-robin continues from.
* In *Notify users*, **Routing Enabled** takes a user in or out of
  round-robin.

Salespeople work from *WhatsApp › Inbox*: *My Conversations* and
*Unassigned*. Managers also have *All Conversations*.

## Known limitations

* Templates with an image, video or document header can't be submitted to
  Meta from Odoo. Create them in WhatsApp Manager, then **Sync Templates**.
* Templates with a location header can be submitted but not sent from Odoo.
* *Tracked* URL buttons are sent like dynamic ones: there is no click
  tracking.
* Reading a message in Odoo is not reported to WhatsApp, so the customer does
  not see it as read.
* The customer is not a member of the Discuss conversation (SPEC.md, D4).
* Uninstalling the module deletes its WhatsApp conversations (D5).
* Not yet checked against a live Meta number, and the side-by-side check with
  Odoo Enterprise's WhatsApp app (SPEC.md §65, D7) is still owed before
  customers use it.

## Tests

```
odoo -d <db> -i whatsapp_connector --test-enable --test-tags /whatsapp_connector --stop-after-init
```

The browser tours need Chrome or Chromium (`ODOO_BROWSER_BIN` if it is not on
the path). Meta is never called: its HTTP calls are mocked.

## License

GPL-3.0. © 2026 Odin. Source: https://github.com/fleetapps/odoo-apps
