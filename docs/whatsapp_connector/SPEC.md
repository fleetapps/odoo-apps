# Odoo 19 WhatsApp Connector

**Enterprise-Compatible Mode + Optional Lead Routing**

- Document status: Development specification
- Target: Odoo 19 Community
- Integration: Meta WhatsApp Business Platform / Cloud API
- Primary use case: One company WhatsApp number → multiple Odoo users → shared or individually assigned customer conversations
- Initial team: 3 Odoo users
- Priority: Production-grade implementation; Enterprise behavioral compatibility is mandatory
- Revision: reviewed and refined 2026-09-30. Changed text is tagged `[R#]` (refinement validated against official Odoo/Meta sources), `[C#]` (fix of a contradiction inside this spec), `[D#]` (decision by the product owner) or `[N#]` (design note that avoids depending on an unverified fact). Appendix A lists every change with its evidence; Appendix B lists the decisions still open.

## 1. Executive Summary

Build an Odoo 19 WhatsApp connector that integrates a company’s WhatsApp Business Platform number into Odoo.

The connector MUST support two operating modes:

### Mode A — No Lead Routing

This mode MUST behave like the Odoo 19 Enterprise WhatsApp experience for WhatsApp conversations.

It must NOT introduce a custom sales inbox, ownership model, lead assignment, round-robin allocation, or salesperson-specific routing.

Customer-initiated conversations must behave as Odoo Enterprise does:

Customer → company WhatsApp number → Odoo Discuss → WhatsApp conversation/group chat involving the configured WhatsApp channel operators.

[R3] In Enterprise these operators are the account's **Notify users** (Control section). Wherever this spec says the WhatsApp account's "operators", it means the Notify users, and the UI uses that name. [R5] Enterprise also has a company-initiated flow (template first); see §8.1.

The objective is Enterprise compatibility, not merely similar functionality.

### Mode B — Lead Routing

This mode adds a CRM-oriented routing layer.

Incoming WhatsApp conversations are assigned to individual Odoo users.

Example:

```
Customer A → WhatsApp → User 1
Customer B → WhatsApp → User 2
Customer C → WhatsApp → User 3
```

Each salesperson manages their assigned conversations while all customers continue communicating through the same company WhatsApp number.

The underlying WhatsApp identity remains the company’s single WhatsApp Business number.

## 2. Non-Negotiable Requirements

The following are hard requirements.

### 2.1 One WhatsApp number

The system must support:

```
1 WhatsApp Business number
        ↓
Multiple Odoo users
```

Users must NOT need their own WhatsApp numbers.

The customer must always see the company’s WhatsApp Business identity.

### 2.2 WhatsApp Business Platform

The integration must use the official WhatsApp Business Platform / Cloud API.

Do NOT use:

* WhatsApp Web automation
* QR-code sessions
* Selenium
* browser automation
* unofficial WhatsApp APIs
* individual salesperson WhatsApp accounts

The connector must be API/webhook based.

## 3. Architecture

Recommended architecture:

```
                    CUSTOMER
                       │
                       │ WhatsApp
                       ▼
             ┌────────────────────┐
             │ Meta WhatsApp      │
             │ Business Platform  │
             └─────────┬──────────┘
                       │
                  Webhooks/API
                       │
                       ▼
             ┌────────────────────┐
             │ Odoo WhatsApp      │
             │ Connector          │
             └─────────┬──────────┘
                       │
                Operating Mode
                 /            \
                /              \
               ▼                ▼
      NO LEAD ROUTING      LEAD ROUTING
               │                │
               │          Routing Engine
               │                │
               │       ┌────────┼────────┐
               │       ▼        ▼        ▼
               │     User 1   User 2   User 3
               │    (owner = conversation member)
               ▼                ▼
      Odoo Discuss conversation (same record type in both modes)
```

[C5] Both modes end in the same kind of Odoo Discuss conversation (§12). Lead Routing only decides who owns the conversation and who is a member of it (§21, §31); it does not bypass Discuss (§54, §66).

## 4. Settings

Create a WhatsApp configuration area.

Example:

~~Settings → WhatsApp → WhatsApp Business Accounts~~

[R1] **WhatsApp app → Configuration → WhatsApp Business Accounts**, as in Enterprise. The WhatsApp app also has a **Templates** menu and a **Messages** menu; the Messages list offers optional **Failure Type** and **Failure Reason** columns.

The account configuration must contain:

### Connection

[R2] Field labels follow Enterprise:

* Account ID (WhatsApp Business Account ID)
* Phone Number ID
* Access Token
* App ID
* App Secret
* API version [N1] (set by the administrator per account; no "current version" is hard-coded)
* Company
* Phone number
* Display name
* Connection status

[R2] **Receiving Messages** group (read-only): **Callback URL** and **Webhook Verify Token**. Odoo generates both and fills them in when the administrator clicks **Test Connection**; they are then copied into the Meta App Dashboard. Administrators do not type them in.

[R2] **Sending messages** group: **Sync Templates** button (§29).

### ~~Operators~~ Notify users [R3]

A list of Odoo users who are responsible for the WhatsApp channel.

[R3] Enterprise calls this list **Notify users** and shows it in the account's **Control** section. In Mode A it drives the group chat (§9) and notifications (§10). [C2] It is the only per-account user list; Lead Routing marks which of these users are eligible for routing (§39) instead of keeping a second list.

Example:

```
WhatsApp Business Account
Company: Example Ltd
Phone: +254 XXX XXX XXX

Notify users:
 Andrew
 Sarah
 Brian
```

### Meta-side setup [R4] [R37]

* Subscribe the app to these webhook fields: `account_update`, `message_template_quality_update`, `message_template_status_update`, `messages`, `template_category_update` (the Enterprise list) and `user_id_update` [R37] (business-scoped user ID changes, §42).
* Use a permanent **System User** access token with the permissions `business_management`, `whatsapp_business_messaging`, `whatsapp_business_management` and `whatsapp_business_manage_events`. Meta requires `whatsapp_business_messaging` for `messages` webhooks and `whatsapp_business_management` for all other webhook fields.
* The Callback URL must be a fully qualified public `https://` URL.
* [R24] The Meta app must be set to **Live**: in Development mode only test numbers can be messaged, and some webhooks are not sent. Test Connection warns about this (§34).

## 5. Operating Mode Setting

Add:

Conversation Routing

Two mutually exclusive options:

```
○ No Lead Routing
○ Lead Routing
```

Default:

No Lead Routing

The setting must have explanatory help text:

**No Lead Routing**

Use the standard Odoo WhatsApp conversation model. Incoming customer conversations are handled through Discuss by the configured WhatsApp operators. [R3] (UI wording: "…by the account's Notify users.")

**Lead Routing**

Automatically assign incoming WhatsApp conversations to individual Odoo users and link them to CRM leads.

[C1] The mode is stored once, on the WhatsApp Business Account (§53).

[D3] What happens to existing conversations when an account switches between the two modes is not defined yet (Appendix B).

## 6. MODE A — NO LEAD ROUTING

### 6.1 Objective

This is the most important compatibility requirement.

When this mode is selected, the connector should behave as closely as technically possible to Odoo 19 Enterprise’s WhatsApp integration.

Do NOT create a second competing WhatsApp inbox.

Do NOT replace Discuss.

Do NOT create salesperson ownership.

Do NOT assign CRM leads automatically.

Do NOT hide conversations from operators.

## 7. Enterprise Compatibility Reference

Before implementation, the developer MUST create or obtain an Odoo 19 Enterprise reference database with WhatsApp configured.

The developer must test:

1. Odoo Enterprise WhatsApp configuration
2. Customer sends first message
3. Customer sends second message
4. Operator replies
5. Another operator opens the conversation
6. Operator adds another user
7. Customer replies after an active conversation
8. Customer replies after the documented conversation window expires
9. Operator notification behavior
10. Conversation popup behavior
11. Discuss conversation behavior
12. Message composer behavior
13. Attachments
14. Images
15. Documents
16. Templates
17. Delivery status
18. Read status
19. Failed messages
20. Conversation history
21. Customer/contact linking
22. Chatter integration
23. WhatsApp button behavior on records
24. User permissions
25. Notification behavior
26. [R5] Company-initiated conversation: template sent first, customer answers within 15 days
27. [R6] Customer replies after the user has not responded for 15 days (notifications go back to all Notify users)
28. [D4] Whether the customer is a member of the Discuss conversation

The custom connector must then reproduce the observable Enterprise behavior.

If there is any disagreement between this document and an actual Odoo 19 Enterprise reference instance, the Enterprise instance is the behavioral reference for Mode A unless explicitly overridden in this specification.

[R16] Explicit override: customer identity handling (business-scoped user IDs, customers without a visible phone number, §42) is required by Meta and applies in both modes, even where the Enterprise reference behaves differently.

## 8. No Lead Routing — Incoming Message

When:

```
Customer → WhatsApp Business Number
```

the connector receives the Meta webhook.

The system must:

1. Validate the webhook. [R26]
2. Identify the WhatsApp Business Account. [R17] (`entry[].id` is the WhatsApp Business Account ID)
3. Identify the phone number. [R17] (the business number: `changes[].value.metadata.phone_number_id`, plus `display_phone_number`)
4. Identify the WhatsApp customer. [R16] (by business-scoped user ID first, see §42)
5. Match the customer to an Odoo contact where possible.
6. Create the appropriate conversation/thread. [R8] (a `discuss.channel`, see §12)
7. Make the conversation available through Odoo Discuss.
8. Notify the configured WhatsApp operators according to the Enterprise behavior. [R6] (§10)
9. Preserve the WhatsApp message ID.
10. Prevent duplicate message creation.

[R17] One webhook field, `messages`, carries both incoming customer messages and the status updates of messages the business sent. The processor tells them apart by the payload (`messages[]` or `statuses[]`).

The conversation must NOT automatically become:

```
CRM Lead
    ↓
Assigned salesperson
```

unless the customer was already associated with a CRM record through existing Odoo behavior.

### 8.1 Company-initiated conversation [R5]

Enterprise supports two flows: customer-initiated (above) and company-initiated. In the company-initiated flow a user sends a template to one or more customers. If a customer answers within 15 days, a Discuss chat window pops up to begin the conversation. Mode A must reproduce this flow; it is covered by parity scenario 9 (§59).

## 9. No Lead Routing — Group Conversation

The fundamental behavior must be:

```
WhatsApp Account
        │
        ├── Operator A
        ├── Operator B
        └── Operator C
                 │
                 ▼
        Customer conversation
```

All configured operators must be able to participate in the customer-initiated conversation according to the Enterprise model.

Do NOT implement:

```
Customer → automatically assigned to Operator A
```

in this mode.

[R3] "Operators" here are the account's Notify users. [R32] "Group chat" means an Odoo Discuss conversation between those users and the customer's WhatsApp thread. It is not a WhatsApp group: Meta's Groups API (`recipient_type=group`, the `group_*` webhook fields) is out of scope.

## 10. No Lead Routing — Notifications

Notifications must follow the Enterprise model.

At the channel level there is a configured set of users to notify.

When a new customer conversation arrives:

```
New conversation
      ↓
Configured WhatsApp operators notified
```

Once a conversation has been established between a user and the customer, subsequent notifications should follow the Enterprise conversation-specific behavior rather than repeatedly notifying every configured channel operator.

The documented Odoo behavior specifically distinguishes initial channel notifications from notifications to users already participating in the conversation.

[R6] The documented rule, exactly:

1. The set of users to notify is the account's **Notify users** (§4).
2. Once a conversation is initiated between a user and a customer, the Notify users are no longer all notified; only the users in the conversation are.
3. If the user does not respond within 15 days, the customer's next reply is sent again to all the Notify users.

[R9] Member notifications use Odoo's own Discuss notification path, which already handles the `whatsapp_message` message type (§12.1).

This must be tested against Odoo 19 Enterprise.

## 11. No Lead Routing — Adding Users

The conversation must support adding users to an existing WhatsApp conversation.

The UX must follow Odoo’s Discuss/WhatsApp model.

The reference behavior is:

```
Open WhatsApp conversation
        ↓
Expand conversation
        ↓
Add User icon
        ↓
Select Odoo user
        ↓
User joins conversation
```

Do not invent a separate “Assign Lead” UX in this mode.

[R7] Odoo 19 Community Discuss already has this: the **Invite People** action (user-plus icon) on every Discuss conversation. Reuse it; do not rebuild it. (Enterprise's documentation names the icon "Add User".)

## 12. No Lead Routing — Discuss Integration

WhatsApp conversations must be accessible from:

Discuss

The connector must use Odoo’s messaging infrastructure wherever practical rather than creating an isolated messaging application.

The following should feel native:

* conversation list
* conversation selection
* message history
* composer
* notifications
* unread state
* attachments
* users
* timestamps
* message status
* customer identity

### 12.1 How the conversation is built [R8] [R9] [R33]

[R8] A WhatsApp conversation **is a `discuss.channel` record** with its own channel type, added with `selection_add`. This is the extension point Odoo 19 Community's own Live Chat module (`im_livechat`, LGPL) uses for its conversations, so it is clean-room safe:

* Python: add the channel type with `selection_add` and an explicit `ondelete` policy (see below), and extend the `_types_allowing_*` hooks as `im_livechat` does.
* JavaScript: patch `Thread.isChatChannel`, and add a WhatsApp category to the Discuss sidebar through the `DiscussApp` / `DiscussAppCategory` models, as `im_livechat` does for Live Chat.

[R9] [D1] The connector reuses the three selection keys that Odoo's Enterprise WhatsApp app uses:

* `discuss.channel.channel_type = 'whatsapp'`
* `mail.message.message_type = 'whatsapp_message'`
* `ir.actions.server.state = 'whatsapp'` (the "Send WhatsApp" automation action, §13.1)

Odoo 19 Community core already special-cases these keys, so the following work **without any override**. Each one gets a regression test (§58):

1. Channel members get web-push and mention notifications for `whatsapp_message` (`discuss.channel._notify_get_recipients`).
2. "Fetched" message tracking runs for `'whatsapp'` channels (`discuss.channel.channel_fetched`).
3. `whatsapp_message` is included in extra notifications (`mail.thread._notify_get_recipients_for_extra_notifications`).
4. The browser treats `whatsapp_message` as push-handled (`out_of_focus_service.js`).
5. The automation editor shows the `fa-whatsapp` icon for the action (`base_automation`).

Community's JavaScript knows only the `whatsapp_message` message type; it has no handling for the `'whatsapp'` channel type, so the JavaScript patches above are still needed. Module and model **names** stay prefixed with the connector's module name; only these three selection keys are shared.

[R33] Consequences of sharing the keys:

* `selection_add` merges a key that another module already provides instead of rejecting it. The connector and Odoo's Enterprise WhatsApp app would therefore share these keys and see each other's records. **The connector must never be installed alongside Odoo's Enterprise WhatsApp app.** A `pre_init_hook` refuses installation when that module is present (its technical name is recorded in the Enterprise Reference Report, §65); the README warns about it; the migration path to Enterprise is open decision D5 (Appendix B).
* Both fields are required, so each added key must declare an `ondelete` policy other than `set null`, or Odoo refuses to load the module.
  * `message_type = 'whatsapp_message'` → `'set default'`: on uninstall the messages become `'comment'`, so history is kept.
  * `channel_type = 'whatsapp'` → **never** `'set default'`. The default channel type is `'channel'`, and a `'channel'` without an access group is readable by every internal user, so this would expose customer conversations. Use `'cascade'` (what `im_livechat` does) or a function that turns them into private `'group'` channels (decision D5). `discuss.channel.write` refuses to change a channel's type, so such a function has to use SQL.

## 13. No Lead Routing — CRM

No automatic CRM lead creation is required.

However, the connector should support linking a WhatsApp conversation to an existing:

* Contact
* Lead
* Opportunity
* Sales Order
* Invoice
* Other Odoo record where the standard WhatsApp integration supports it

Do not automatically create duplicate CRM records.

### 13.1 Where WhatsApp appears in other apps [R10] [R11]

[R10] Enterprise adds a **WhatsApp** button above the chatter composer on records. If the record's model has approved templates, the button opens a **Send WhatsApp Message** pop-up. This is part of Mode A parity (§59 scenario 8).

[R11] The Odoo 19 documentation lists these WhatsApp touchpoints. The "Proposed" column is a scope proposal; the other columns are verified facts.

| Touchpoint | In Odoo 19 Community? | Proposed |
|---|---|---|
| Chatter WhatsApp button and Send WhatsApp Message pop-up | yes (`mail`) | MVP |
| Canned responses in WhatsApp conversations | yes (`mail.canned.response`) | MVP (works through the Discuss composer) |
| "Send WhatsApp" automation action | yes (`base_automation`; the `sms` module shows how to add an action type) | MVP |
| Event communications by WhatsApp | yes (`event`) | P2 |
| Point of Sale receipts by WhatsApp | yes (`point_of_sale`) | P2 |
| eCommerce checkout invoice template by WhatsApp | yes (`website_sale`) | P2 |
| Helpdesk `/ticket`, Marketing Automation, Payment follow-up | **no**: these apps are not in Odoo 19 Community | N/A |

## 14. MODE B — LEAD ROUTING

Lead Routing adds an ownership layer.

Example:

```
Customer A
   ↓
WhatsApp
   ↓
Incoming conversation
   ↓
Routing engine
   ↓
Andrew
```

The customer still communicates with:

Company WhatsApp Number

not Andrew’s personal number.

[D2] A conversation that a salesperson starts by sending a template from a record (the company-initiated flow, §8.1) is owned by **the salesperson who sent it**. It does not enter round-robin. [D6] If that record is a lead whose salesperson is someone else, the lead is reassigned to the sender (§18).

## 15. Lead Routing — CRM Lead Creation

For a new WhatsApp customer/conversation:

1. Identify WhatsApp phone number. [R16] Identify the customer by business-scoped user ID first; the phone number is used when WhatsApp provides it (§42).
2. Search Odoo contacts.
3. Search existing CRM leads/opportunities associated with that phone number. [R14] Odoo's own CRM duplicate check matches only on email or contact, not phone, so the connector searches contacts and leads by phone itself, using the indexed `phone_mobile_search` / `phone_sanitized` fields.
4. If an appropriate existing record exists, link the conversation.
5. Otherwise create a CRM Lead.

Minimum lead fields:

```
Lead Name
Contact Name
Phone
~~Mobile~~
Email, if available
Source = WhatsApp
WhatsApp Conversation
Assigned User
Sales Team
Created Date
Last WhatsApp Message
```

[R12] Odoo 19 has no `mobile` field on leads or contacts, so "Mobile" is dropped. The fields map to `crm.lead` as: Lead Name → `name`; Contact Name → `contact_name`; Phone → `phone` (Odoo computes the E.164 form into `phone_sanitized`); Email → `email_from`; Source → `source_id`; Assigned User → `user_id`; Sales Team → `team_id`; Created Date → `create_date`; WhatsApp Conversation and Last WhatsApp Message are connector fields. [R16] A lead may have no phone at all (customers who hide their number behind a WhatsApp username, §42).

[R13] "Source = WhatsApp": Odoo 19 Community ships no WhatsApp source, so the connector ships its own `utm.source` record "WhatsApp" (by XML ID).

[R21] If the first message came from a Click-to-WhatsApp ad or post, the message carries a `referral` object (ad/post `source_id`, `source_type`, `headline`, `body`, `ctwa_clid`, …). Keep it on the first message and on the lead for attribution (P1).

Recommended lead name:

```
WhatsApp — <Customer Name>
```

If customer name is unavailable:

```
WhatsApp — +254XXXXXXXXX
```

[R16] If neither a name nor a phone number is available: `WhatsApp — <WhatsApp username>`, or the business-scoped user ID when there is no username either.

## 16. Lead Routing — Assignment

The first implementation must support:

Round Robin

Example:

```
User 1
User 2
User 3
```

Incoming conversations:

```
Conversation 1 → User 1
Conversation 2 → User 2
Conversation 3 → User 3
Conversation 4 → User 1
```

The round-robin cursor must be persisted.

It must NOT reset when Odoo restarts.

[R29] The cursor is read and advanced under a row lock (`lock_for_update()` in Odoo 19), so two conversations arriving at the same moment cannot get the same user.

[D2] Company-initiated conversations (§8.1) never enter round-robin: the sender owns them.

## 17. Assignment Rules

The routing engine must only assign to eligible users.

Eligibility:

```
User is active
AND
User has WhatsApp access
AND
User is enabled for this routing configuration
```

Optional future fields:

```
Maximum active conversations
Working hours
Team
Country
Language
Product
Lead source
```

Do not implement these advanced rules unless explicitly enabled.

## 18. Manual Assignment

Administrators/managers must be able to change ownership.

Example:

```
Assigned to:
[ Andrew ▼ ]
```

Changing the salesperson must:

1. Update the CRM lead.
2. Update conversation ownership.
3. Notify the newly assigned user.
4. Preserve the entire conversation history.
5. NOT change the customer’s WhatsApp number.
6. NOT create a new WhatsApp conversation.

[R31] [D6] The conversation owner and the lead's salesperson (`crm.lead.user_id`) are always the same person, and the link works in both directions:

* Changing the owner here updates the lead.
* Changing the salesperson on the lead (by hand, or by Odoo CRM's own rule-based assignment, which picks up leads without a salesperson) moves the conversation to that user.
* When a user who is not the lead's salesperson starts a WhatsApp conversation from that lead (§14), the lead is reassigned to that user. The change is logged in the lead's chatter and the audit trail (§52), and the previous salesperson is notified.
* When Odoo CRM merges duplicate leads, the conversation's lead link follows the lead that survives the merge.

## 19. Lead Routing — User Inbox

When Lead Routing is enabled, provide:

WhatsApp → Inbox

with:

```
My Conversations
Unassigned
All Conversations
```

### My Conversations

Only conversations owned by the current user.

### Unassigned

Conversations waiting for assignment.

### All Conversations

Available to managers/admins.

Normal sales users should not automatically see every salesperson’s private conversations unless their access rights permit it.

[C5] The Inbox is a filtered entry point (list/kanban views and filters) into the same Discuss conversations (§12.1). The chat itself is the Discuss conversation, not a second chat engine.

[R30] Visibility comes from Odoo's Discuss access rules: a WhatsApp conversation (any channel type other than `channel`) is readable only by its members. "My Conversations" is therefore the conversations the user is a member of and owns; "All Conversations" is granted to WhatsApp Managers by one extra record rule limited to the WhatsApp channel type. Note that Odoo Settings administrators (`base.group_system`) can read every Discuss conversation regardless of membership.

## 20. Conversation Ownership

Every routed conversation must have:

```
conversation_id
customer_id
whatsapp_phone_number_id
odoo_partner_id
crm_lead_id
assigned_user_id
assigned_team_id
status
created_at
last_message_at
last_customer_message_at
last_user_message_at
```

Ownership must be independent of the WhatsApp phone number.

One WhatsApp number can therefore serve hundreds/thousands of conversations with different owners.

[C3] [R8] [R16] How these map onto the data model (§53): `conversation_id` is the `discuss.channel` record; `customer_id` is the customer's business-scoped user ID (with the WhatsApp ID and phone number stored alongside when WhatsApp provides them); `whatsapp_phone_number_id` is the business number's Phone Number ID on the account. [R18] Meta's `conversation` object (sent only with status updates, next to pricing data) is not used as a conversation identifier.

## 21. Ownership Rule

There must be only one primary owner.

```
Conversation
    ↓
Primary owner = User A
```

Additional users can optionally be given access by an administrator/manager.

Do not turn a routed conversation into an unrestricted group chat by default.

[R30] Odoo 19 Community lets any member of a Discuss conversation invite others: the record rule "internal users can invite others in channels they are member of" covers every channel type except `channel` and `chat`, so it covers `whatsapp` too, and the Invite People action is offered on every Discuss conversation. For routed conversations the connector restricts inviting to WhatsApp Managers, both on the server (override `add_members`) and in the UI.

## 22. Customer Returning Later

This is critical.

Suppose:

```
Monday:
Customer → User A
```

Later:

```
Thursday:
Customer sends another message
```

The system must NOT automatically create another lead or assign the customer to User B simply because a new inbound message arrived.

The connector must first identify the existing customer/conversation.

Default rule:

```
Existing active conversation
        ↓
Keep existing owner
```

Only a genuinely new conversation/customer routing event should enter round-robin allocation.

[R16] The existing conversation is found by the customer's business-scoped user ID first (§42). Matching on phone number alone is not enough: a customer who uses a WhatsApp username can arrive without a phone number (Meta includes it only if this business number messaged or called them, or received a message or call from them, in the last 30 days, or if they are in Meta's contact book), and Meta warns that such messages "may look like a new user thread".

## 23. Reassignment

If User A owns:

```
Customer → User A
```

and manager changes ownership:

```
User A → User B
```

then future messages go to User B.

The existing conversation remains intact.

No new WhatsApp thread should be created.

[R31] The same applies when the salesperson is changed on the lead itself (§18).

## 24. Duplicate Prevention

This is mandatory.

Meta may retry webhook events.

The system must be idempotent.

Store the external WhatsApp message ID.

Example:

```
wa_message_id
```

Before creating a message:

```
IF wa_message_id already exists
    ignore duplicate
ELSE
    create message
```

The same principle applies to status events.

[R35] Meta confirms it: a webhook that does not get HTTP 200 is retried "with decreasing frequency until the request succeeds, for up to 7 days", and "these retries can result in duplicate webhook notifications". [R19] A status that arrives late never overwrites a later one (for example, `delivered` arriving after `read` is ignored).

## 25. Message Model

Every WhatsApp message should retain:

```
External WhatsApp Message ID
Conversation ID
Direction
Message Type
Sender
Recipient
Body
Media ID
Attachment
Timestamp
Status
Error Code
Error Message
Created At
```

Direction:

```
INBOUND
OUTBOUND
```

Status:

```
QUEUED
SENT
DELIVERED
READ
FAILED
```

Do not fabricate statuses where Meta has not supplied one.

[C4] The §53 message model carries all the fields listed above (sender, recipient, error title and created-at were missing there).

[R19] What Meta actually supplies:

* `QUEUED` is an Odoo-side state only: the message is saved but not yet accepted by Meta.
* The send API response returns the message ID (`messages[].id`) and `contacts[]` (`input`, `wa_id`; Meta notes the two "may not match"). The response "only indicates that the API successfully accepted your request — it does not indicate successful delivery". A `message_status` field (`accepted`, `held_for_quality_assessment`, `paused`) appears **only** when sending a template that is being paced; store it when present.
* Status webhooks carry `sent`, `delivered`, `read` or `failed`, with `errors[]` (`code`, `title`, `message`, `error_data.details`, `href`) on failures. Store all of them. For `failed` statuses the `contacts` block is omitted, so the status is matched by message ID only.
* Meta's message time-to-live is 30 days (10 minutes for authentication templates). Meta says: "If you do not receive a status messages webhook with status set to delivered before the TTL is exceeded, assume the message was dropped." The connector shows such messages as **dropped**, next to failed ones; this is Meta's own rule, not an invented status.

## 26. Supported Message Types

MVP must support:

* Text
* Image
* Document
* Video
* Audio
* Location
* Contact, if supported by API
* WhatsApp templates
* Interactive/template buttons where supported

The developer must map each supported Meta message type to an appropriate Odoo representation.

[R20] The complete list of incoming message types in Meta's API, and their mapping:

| Meta type | Odoo representation |
|---|---|
| `text` | message body |
| `image`, `video`, `audio`, `document` | attachment (§51) with caption as body |
| `sticker` | image attachment |
| `location` | body with name, address and a map link |
| `contacts` | body with the shared contact details |
| `reaction` | Discuss reaction (`mail.message.reaction`) on the message it refers to |
| `button` (reply to a template quick-reply button) | body showing the button text / payload |
| `interactive` (`button_reply`, `list_reply`) | body showing the chosen title |
| `order` | body with a text summary of the ordered items |
| `system` | identity update (§42), plus a short note in the conversation |
| `unsupported` / `unknown` | placeholder showing Meta's error title |

* When an incoming message carries `context.id`, it is a reply to that message: link it through `mail.message.parent_id`.
* Outgoing replies send `context.message_id`.
* "Mark as read" sends `status: "read"` for the message, optionally with a `typing_indicator`.

## 27. Outbound Messages

A salesperson sends:

```
Odoo
 ↓
WhatsApp Connector
 ↓
Meta Cloud API
 ↓
Customer
```

The customer sees the company’s WhatsApp Business number.

Never expose:

* Odoo user’s phone number
* Odoo user’s WhatsApp account
* internal Odoo email
* internal user identity unless deliberately included in message content

[R36] Addressing the customer:

* Send to the phone number in E.164 form **with the "+" and country code** (§42). Meta: "If the plus sign is omitted, your business phone number's country calling code is prepended to the customer's phone number. This can result in undelivered or misdelivered messages."
* When there is no phone number, send to `recipient` = the business-scoped user ID. If both `to` and `recipient` are given, Meta uses the phone number.
* Meta documents sending to business-scoped user IDs as supported "starting in July 2026" while the request section still says "coming soon", so it is behind a per-account on/off setting. Error `131062` ("Business-scoped User ID (BSUID) recipients are not supported for this message", for example authentication templates, which need a phone number) is shown on the message, and the send is retried by phone when one is known.
* If the access token can reach more than one Messaging account on the business number, the request includes `messaging_account_id`.

## 28. WhatsApp Conversation Window

The implementation must respect the actual Meta/WhatsApp messaging rules.

The developer must NOT hard-code assumptions about conversation windows without verifying the current Meta API behavior.

Odoo’s current documentation describes its own WhatsApp conversation behavior around a 15-day response period. This must be tested against the exact Odoo 19 Enterprise reference implementation and the current Meta API behavior.

Where Meta requires a template for an outbound message, the UI must prevent/handle an invalid free-form outbound send rather than silently failing.

[R34] Two different windows, verified against the current documentation:

1. **Meta's customer service window** decides whether a free-form message may be sent. Meta: "When a WhatsApp user messages you or calls you, a 24-hour timer called a customer service window starts. If the user messages or calls you again before the timer expires, the timer resets to 24 hours." "When the window closes, you can only send pre-approved template messages." Meta also lists a known issue: "In rare cases, you may receive a message from a WhatsApp user but be unable to respond within the customer service window", so the UI must also handle a rejected free-form send while the window looks open.
2. **Odoo's own 15-day periods** are Odoo behaviors, not Meta rules: the chat window that pops up when a customer answers a template within 15 days (§8.1), and the return to notifying all Notify users after 15 days without a response (§10).

## 29. Templates

Support Meta-approved WhatsApp templates.

Template records should include:

```
Name
Language
Category
Meta Template ID
Status
Header
Body
Footer
Buttons
Variables
```

Support syncing approved templates from Meta.

Template variables must be resolvable against the Odoo record being messaged.

[R25] Enterprise template features, to match in Mode A:

* Fields: Name, Language, **Account**, **Applies to** (the Odoo model), **Phone Field** (which field of that model holds the number), allowed **Users**, **Category** (Marketing, Utility, Authentication).
* Header types: Text, Image, Video, Document, Location.
* Buttons: Quick Reply; Visit Website (static, dynamic or tracked URL); Call Number.
* Variables: placeholders `{{1}}`, `{{2}}`, … filled from a **Field of Model**, a **Portal link**, and the other variable types Enterprise offers.
* Templates can be created in Odoo and sent to Meta with **Submit for approval** (status becomes Pending), not only synced from Meta.
* Sync all templates from the account (**Sync Templates**) or one template from its own form (**Sync Template**).
* Status, quality and category changes arrive through the webhook fields listed in §4.
* "Multi-Template" allows sending a template to several contacts at once.

[N2] The template status is stored exactly as Meta sends it. The UI labels the statuses it knows (for example `APPROVED`, `PENDING`, `REJECTED`) and shows any other value as received, so a status Meta adds later is never lost.

## 30. Chatter Integration

Where the WhatsApp conversation is associated with a CRM lead/contact, messages should be traceable from the associated Odoo record.

Example:

```
CRM Lead
  ↓
Chatter
  ↓
WhatsApp conversation/message history
```

Do not create duplicate copies of the same message unnecessarily.

There should be one canonical WhatsApp message record.

Chatter representations should reference that message.

## 31. User Permissions

Create explicit security groups.

### WhatsApp User

Can:

* access assigned conversations
* send WhatsApp messages
* receive WhatsApp messages
* view linked contacts/leads
* upload supported attachments

### WhatsApp Manager

Can:

* view all conversations
* assign/reassign conversations
* configure routing
* configure operators
* manage WhatsApp accounts
* manage templates
* inspect failed messages

### WhatsApp Administrator

Can:

* configure Meta credentials
* configure webhooks
* manage all WhatsApp settings
* manage users
* manage routing
* troubleshoot integration

[R30] These groups work on top of Odoo's Discuss access rules (§19): a WhatsApp conversation is readable only by its members; WhatsApp Managers get one extra record rule for all WhatsApp conversations; Odoo Settings administrators (`base.group_system`) can read every Discuss conversation regardless.

## 32. Security

Meta credentials must never be exposed to normal users.

Access tokens must:

* be stored securely
* never be displayed in plaintext after saving
* never be included in frontend API responses
* never appear in normal Odoo chatter
* never appear in logs

Webhook requests must be validated.

Webhook endpoints must reject invalid verification/signature requests.

[R26] How, per Meta's documentation:

* **Verification (GET):** Meta calls the Callback URL with `hub.mode=subscribe`, `hub.challenge` and `hub.verify_token`. If `hub.verify_token` matches the stored Webhook Verify Token, "respond with HTTP status 200 and the hub.challenge value"; otherwise respond with a 400-level status.
* **Events (POST):** the `X-Hub-Signature-256` header carries `sha256=` followed by an "HMAC-SHA256 hash, calculated using the post body payload and your app secret as the secret key". Compute it over the raw request body (`request.httprequest.get_data()`) before parsing the JSON, and compare with `hmac.compare_digest`. Valid → 200; invalid → 400-level.
* The Odoo route is `type='http'`, `auth='public'`, **`csrf=False`**: Odoo checks a CSRF token on every POST to an `http` route unless `csrf=False`. Do not use `type='json'`, which Odoo 19 has deprecated as an alias of `jsonrpc`.

[R4] The token is a permanent System User token with the permissions listed in §4.

## 33. Webhook Reliability

Incoming webhook processing must be resilient.

The webhook endpoint should:

1. Validate request.
2. Parse event.
3. Persist event/message.
4. Return successful response quickly.
5. Process downstream Odoo actions safely.

Do not perform long-running work synchronously if it risks Meta webhook timeouts.

Use queued/background processing where appropriate.

[R35] What Meta's documentation says about delivery:

* A request that does not get HTTP 200 is retried "with decreasing frequency until the request succeeds, for up to 7 days", which "can result in duplicate webhook notifications" (§24). "Unacknowledged responses will be dropped after 7 days."
* "There are no APIs for fetching historical webhook data, so capture and store webhook payloads accordingly." The stored raw event is the only copy; its retention period is set by an administrator.
* "POST requests are aggregated and sent in a batch with a maximum of 1000 updates. However, batching cannot be guaranteed." One POST is stored as one raw event and split into one processing item per update.
* "Webhook payloads can be up to 3 MB." Odoo's default request size limit is 128 MiB, so no change is needed.

[R27] Odoo 19 Community has no job queue. The endpoint validates the signature, stores the raw event, returns 200, and triggers a processing cron with `ir.cron._trigger()` (it runs the next time a cron worker wakes up). The cron processes events in batches and commits with `_commit_progress()`. Deployment needs cron workers enabled (`--max-cron-threads` greater than 0; the default is 2).

## 34. Connection Health

The WhatsApp account configuration should show:

```
● Connected
● Connection Error
● Webhook Error
● Token Error
```

Provide:

Test Connection

The test must actually validate the configured credentials/API rather than merely checking that fields are non-empty.

[R24] Test Connection:

* Calls `GET /{Phone-Number-ID}?fields=status,display_phone_number,verified_name,quality_rating`. Meta: the number "must have a status of 'connected' in order to send and receive messages via the API"; any other status is shown as a Connection Error.
* Calls `GET /{WABA-ID}/subscribed_apps` to check that the app is subscribed to the account (Odoo's documentation uses this call to fix "cannot receive messages"); a missing subscription is shown as a Webhook Error.
* An authentication error from Meta is shown as a Token Error.
* Generates the Callback URL and Webhook Verify Token (§4).
* Warns that the Meta app must be Live (§4).

## 35. Error Handling

Failed outbound messages must be visible.

Example:

```
Message failed
[Retry]
```

Store:

```
Meta error code
Meta error title
Meta error message
Timestamp
```

Do not silently discard failed messages.

[R19] Also store Meta's `error_data.details`, and show messages Meta considers dropped (no `delivered` status within its time-to-live, §25) next to failed ones.

## 36. Enterprise UX Parity Requirement

This is a HARD acceptance requirement.

For No Lead Routing, the developer must not design the UX from imagination.

The developer must:

1. Install Odoo 19 Enterprise in a reference environment.
2. Configure WhatsApp.
3. Create three operators.
4. Send real test messages.
5. Record the resulting UI.
6. Inspect the Discuss interface.
7. Inspect notification behavior.
8. Inspect conversation membership.
9. Inspect message states.
10. Inspect attachments.
11. Inspect templates.
12. Inspect adding users.
13. Inspect CRM/chatter behavior.
14. Implement the Community connector.
15. Repeat the exact tests.
16. Compare results.

Create a parity checklist with:

```
Enterprise behavior
Custom connector behavior
PASS / FAIL
Evidence
```

No Lead Routing is not considered complete until every applicable parity test passes.

## 37. Do Not Clone Enterprise Internals Blindly

The goal is:

Behavioral and UX compatibility.

It is NOT:

Copy Odoo Enterprise proprietary source code.

The implementation must use clean-room-compatible code and Odoo Community APIs/extension points.

Do not copy proprietary Enterprise source code.

[R8] The Community reference pattern for a custom Discuss conversation type is Odoo's own Live Chat module (`im_livechat`, LGPL-3), see §12.1. [R9] [R33] Reusing Enterprise's three selection keys is not copying code, but it means the connector must never be installed together with Odoo's Enterprise WhatsApp app (§12.1).

## 38. Lead Routing UX

When Lead Routing is enabled, the custom routing UI may differ from Enterprise.

It should be optimized for sales operations.

Recommended layout:

```
┌──────────────────────────────────────────────────────────────┐
│ WhatsApp Inbox                                               │
├────────────────┬─────────────────────────────────────────────┤
│ My Chats (12)  │ Customer                                    │
│ Unassigned (3) │ +254 7XX XXX XXX                            │
│ All Chats      │                                             │
│                │ Assigned to: Andrew                         │
│ John           │ Lead: WhatsApp — John                       │
│ Sarah          │ Stage: New                                  │
│ Brian          │                                             │
│                │ ------------------------------------------- │
│                │ Customer: Hi, I want information...         │
│                │                                             │
│                │ Andrew: Sure, how can I help?               │
│                │                                             │
│                │ Customer: How much is the product?          │
│                │                                             │
│                │ [ Type a message...                    ]    │
└────────────────┴─────────────────────────────────────────────┘
```

The custom routed inbox must still feel native to Odoo.

[C5] The right-hand chat pane is the Discuss conversation itself (§12.1, §19), embedded or opened from the list; the inbox adds the owner, lead and stage panel around it.

## 39. Routing Configuration

Configuration UI:

```
WhatsApp Business Account

Conversation Routing

( ) No Lead Routing
    Standard Odoo/Enterprise-compatible behavior

(•) Lead Routing
    Assign new conversations to Odoo users

Routing method:
[ Round Robin ▼ ]

Eligible users:
 User A
 User B
 User C

Fallback user:
[ Manager ▼ ]
```

[C1] "Conversation Routing" is the mode field on the WhatsApp Business Account (stored once). [C2] "Eligible users" is not a second user list: it is the account's Notify users (§4) with a "routing enabled" flag per user.

## 40. Fallback Behavior

If no eligible user is available:

```
New conversation
       ↓
No eligible salesperson
       ↓
Unassigned
       ↓
Manager notification
```

Never silently lose the conversation.

## 41. Routing State

Persist:

```
routing_configuration_id
last_assigned_user_id
```

For round robin:

```
A → B → C → A → B → C
```

If B is disabled:

```
A → C → A → C
```

The algorithm must skip inactive/ineligible users.

[R29] The cursor update runs under a row lock (§16).

## 42. Existing Customer Logic

Customer identity matching should prioritize:

* ~~1. WhatsApp phone number~~
* ~~2. normalized mobile number~~
* ~~3. existing WhatsApp conversation~~
* ~~4. linked Odoo contact~~
* ~~5. linked CRM lead/opportunity~~

[R16] New order:

1. Existing WhatsApp conversation with the same **business-scoped user ID** (BSUID)
2. Existing conversation with the same WhatsApp ID (`wa_id`) or normalized E.164 phone number (conversations from before BSUIDs)
3. Odoo contact or lead whose `phone_sanitized` matches the normalized phone number [R12] (there is no mobile field in Odoo 19)
4. Linked CRM lead/opportunity

[R16] Why, from Meta's current documentation (business-scoped user IDs, updated June 29, 2026):

* Every incoming message webhook carries the customer's BSUID ("BSUID will be assigned to the `user_id` parameter and appear in all messages webhooks, regardless of whether or not the user has enabled the username feature"): `contacts[].user_id` and `messages[].from_user_id`.
* The phone number (`wa_id`, `from`) can be missing. WhatsApp users can adopt a username, and their number is then included only if this business number exchanged messages or calls with them in the last 30 days, or if they are in Meta's contact book.
* Meta: supporting BSUIDs "is required for all partners and directly-integrated businesses", and "you must support BSUID to avoid losing the ability to process their messages".
* A BSUID is the customer's two-letter country code, a period, and up to 128 letters and digits (for example `US.13491208655302741918`). It is specific to the business portfolio and is "regenerated if a user changes their phone number".
* Businesses enrolled for it also receive a parent BSUID (`parent_user_id`, for example `US.ENT.…`); usernames arrive in `contacts[].profile.username`.
* Meta also notes that "any changes described in this document are subject to change", so the parser must accept payloads where any of these fields is missing.

Rules that follow:

* A conversation is identified by (account, BSUID). The WhatsApp ID, phone number, parent BSUID and username are stored with it when present.
* A contact or lead may have **no phone number**. It is created with the WhatsApp profile name and username. When a phone number arrives later (for example a shared contact with `origin = contact_request`, or `wa_id` appearing in a webhook), it is filled in and possible duplicates are flagged for a person to review, never merged automatically.
* When the BSUID changes (system message `user_changed_user_id`, or the `user_id_update` webhook with `user_id.previous` and `user_id.current`), the existing conversation is re-keyed to the new BSUID. The system message `user_changed_number` updates the stored phone number. None of these create a new conversation, contact or lead.

Phone numbers must be normalized to E.164 where possible.

Example:

```
0712 345 678
+254 712 345 678
254712345678
```

should resolve to the same normalized number where country context makes this unambiguous.

[R15] Normalization reuses Odoo's `phone_validation` module: `_phone_format(number=…, force_format='E164')`, which falls back to the company's country when the record has none. That module relies on the `phonenumbers` Python library, which is **optional** in Odoo 19 (without it, numbers are returned unchanged), so the connector declares `external_dependencies: {'python': ['phonenumbers']}` in its manifest and adds it to this repository's `requirements.txt`.

## 43. New vs Existing Conversation

Define:

### New conversation

No existing open/active WhatsApp conversation exists for the customer. [R16] ("The customer" is identified as in §42, by business-scoped user ID first.)

### Existing conversation

A previous conversation exists and remains associated with the customer.

Routing occurs only when appropriate for a new conversation.

Existing conversation ownership is preserved.

[D2] A new conversation started by a salesperson with a template is owned by that salesperson and is not routed (§14).

## 44. Closing Conversations

Lead Routing mode must support:

```
Open
Closed
```

Closing a conversation does not delete its history.

If the customer later sends a new message:

```
Customer returns
       ↓
Existing customer identified
       ↓
Closed conversation detected
       ↓
New routing event
```

Whether the old conversation is reopened or a new conversation is created must be explicitly tested and defined during implementation.

Default recommended behavior:

Reopen the existing customer thread while creating a new CRM activity/lead event where appropriate.

Do not create duplicate contacts.

## 45. Manager View

Managers should have:

```
All Conversations
```

Columns:

```
Customer
Phone
Assigned User
Lead
Status
Last Message
Last Message Time
Unread
```

Filters:

```
My Conversations
Unassigned
Assigned
Open
Closed
Unread
User
Date
Lead Stage
```

[R16] The Phone column can be empty (customers with a WhatsApp username and no visible number); show the username in that case.

## 46. Notifications

Lead Routing mode:

New assignment:

```
New WhatsApp Lead
Customer: John
Assigned to: Andrew
```

The assigned salesperson receives the Odoo notification.

When the customer replies, the assigned salesperson receives the conversation notification.

Other salespeople should not receive normal notifications for another user’s assigned conversation.

Managers may receive notifications according to their configured permissions/settings.

[R30] Discuss notifies the members of a conversation, so this follows from membership: in Lead Routing the owner is the conversation member (plus anyone a manager added), and other salespeople are not members.

## 47. Search

Search conversations by:

* customer name
* phone number
* lead name
* message content
* assigned user

Phone-number search is particularly important.

[R14] Phone search uses Odoo's `phone_mobile_search` field (from `phone_validation`), which is indexed and matches numbers regardless of formatting. [R16] Conversations can also be searched by WhatsApp username and business-scoped user ID.

## 48. Performance

The system should support at minimum:

```
1 WhatsApp number
3–50 Odoo users
10,000+ conversations
100,000+ messages
```

The architecture must not assume only three users.

Do not load entire conversation history into the browser.

Use pagination/incremental loading.

[R28] Discuss already does this: it loads 30 messages at a time and pages by message ID.

## 49. Real-Time Updates

New messages should appear without requiring a browser refresh.

Use Odoo’s appropriate real-time bus infrastructure.

Example:

```
Customer sends WhatsApp
        ↓
Meta webhook
        ↓
Odoo
        ↓
Assigned user's browser
        ↓
New message appears
```

## 50. Message Ordering

Messages must retain their correct chronological order.

Webhook delivery order cannot be assumed.

Use:

* WhatsApp message timestamp
* external message ID
* persisted server timestamps

to maintain deterministic ordering.

[R28] How Discuss orders messages: by `mail.message` ID, both on the server (`_order = 'id desc'`; loading and paging by ID) and in the browser (messages sorted by ID). The WhatsApp timestamp therefore cannot reorder what Discuss shows. The connector:

* sets the message `date` to the WhatsApp timestamp, so the time shown is correct;
* within one processing batch, creates each conversation's messages in (WhatsApp timestamp, message ID) order;
* accepts that a message whose webhook arrives late (for example after Meta's retries, §24) appears below newer messages, with its true timestamp. The parity report records what Enterprise does in the same case.

[R19] For outgoing messages Meta also states that "the order in which messages are delivered is not guaranteed to match the order of your API requests"; a sequence that must arrive in order has to wait for each `delivered` status before sending the next message.

## 51. Media

Incoming media must:

1. Be received from Meta.
2. Be downloaded securely.
3. Be attached to the corresponding Odoo message.
4. Preserve MIME type.
5. Preserve filename where available.
6. Not expose Meta media URLs directly to end users if temporary/unsafe.

Outbound media must use the correct WhatsApp API flow.

[R22] Meta's media rules and what the connector does:

* Downloading is two steps: `GET /{Media-ID}` (optionally with `phone_number_id` to check the media belongs to the number) returns a URL, which is then downloaded with the access token. "Media URLs expire after 5 minutes", and "If you omit your token, the request will fail". On `404`, get a new URL and retry; if that still fails, the token needs renewing.
* "Media IDs in webhooks expire after 7 days" (IDs returned by an upload last 30 days). The download therefore runs in the background immediately after the webhook; if it has not succeeded within 7 days it cannot succeed any more, and the message shows that.
* Check the `sha256` sent by Meta, keep `mime_type` and the filename, store the file as an `ir.attachment`, and never show Meta's URLs to users.
* A customer file over 100 MB arrives as error `131052`. Show it on the conversation so the user can ask the customer for a smaller file.
* Before uploading, check the type and size against Meta's supported media, and block anything else with a clear message:

| Type | Formats | Max size |
|---|---|---|
| Image | JPEG, PNG (8-bit, RGB or RGBA) | 5 MB |
| Audio | AAC, AMR, MP3, M4A, OGG (Opus codec only) | 16 MB |
| Video | 3GP, MP4 (H.264 video and AAC audio only) | 16 MB |
| Document | TXT, XLS, XLSX, DOC, DOCX, PPT, PPTX, PDF | 100 MB |
| Sticker | WebP (only in sticker messages) | 100 KB static, 500 KB animated |

* A MIME type that does not match the file is error `131053`.

## 52. Audit Trail

Record:

* assignment changes
* conversation creation
* conversation closure
* reassignment
* template sends
* failed sends
* webhook errors

Example:

```
15:32 — Conversation assigned to Andrew
15:48 — Andrew sent message
15:49 — Message delivered
15:51 — Customer replied
16:02 — Manager reassigned conversation to Sarah
```

[D6] Also record: a lead reassigned to a salesperson who started a WhatsApp conversation from it (§18). [R16] Also record: a conversation re-keyed to a new business-scoped user ID, and a phone number filled in later.

## 53. Data Model — Minimum

[C1] [C2] [C3] [C4] The refined data model in §53.1 replaces the lists below, which are kept as originally written.

Recommended models:

### `whatsapp.account`

```
name
company_id
phone_number
phone_number_id
waba_id
access_token
app_id
app_secret
webhook_verify_token
routing_mode
active
```

### `whatsapp.operator`

```
account_id
user_id
active
routing_enabled
```

### `whatsapp.conversation`

```
account_id
wa_customer_phone
partner_id
lead_id
assigned_user_id
status
external_conversation_reference
last_message_at
last_customer_message_at
last_user_message_at
```

### `whatsapp.message`

```
conversation_id
external_message_id
direction
message_type
body
media_id
attachment_id
status
error_code
error_message
wa_timestamp
```

### `whatsapp.routing.config`

```
account_id
mode
method
eligible_user_ids
fallback_user_id
last_assigned_user_id
```

The developer may adapt model names to Odoo conventions.

### 53.1 Refined data model [C1] [C2] [C3] [C4] [R8] [R9] [R16] [R18]

[R9] Model names use the connector's module prefix, written `<module>` below; only the selection keys of §12.1 are shared with Enterprise.

`<module>.account` (was `whatsapp.account`)

```
name
company_id
phone_number
phone_number_id
waba_id
access_token            (readable by WhatsApp Administrators only, §32)
app_id
app_secret              (readable by WhatsApp Administrators only, §32)
webhook_verify_token    (generated by Odoo, §4)
api_version             [N1]
routing_mode            [C1] the only place the mode is stored
bsuid_sending_enabled   [R36]
active
```

`<module>.operator` (was `whatsapp.operator`): the account's Notify users [R3]

```
account_id
user_id
active
routing_enabled         [C2] replaces routing.config.eligible_user_ids
```

The conversation is a `discuss.channel` with `channel_type = 'whatsapp'` [R8] [R9] (so `conversation_id` is the channel's ID and `created_at` its `create_date` [C3]), extended with the fields below. They are added to a model other modules also extend, so every field carries the `wa_` prefix [R9]:

```
wa_account_id
wa_bsuid                     [R16] identity key: one open conversation per (account, BSUID)
wa_parent_bsuid              [R16] only when the business is enrolled
wa_id                        [R16] when WhatsApp provides it
wa_customer_phone            E.164, when WhatsApp provides it
wa_username                  [R16] when the customer has a username
wa_partner_id
wa_lead_id
wa_assigned_user_id          [R31] equal to wa_lead_id.user_id whenever a lead is linked
wa_assigned_team_id          [C3]
wa_status                    open / closed
wa_last_message_at
wa_last_customer_message_at  (drives Meta's customer service window, §28)
wa_last_user_message_at
wa_referral                  [R21] Click-to-WhatsApp ad data, when present
```

~~`external_conversation_reference`~~ is removed [R18]: Meta's `conversation` object is not a conversation identifier (§20).

`<module>.message` (was `whatsapp.message`): WhatsApp transport data for one `mail.message` of type `whatsapp_message` [R9]; the `mail.message` is the canonical message shown in Discuss and chatter (§30)

```
mail_message_id
conversation_id           (the discuss.channel)
external_message_id
direction
message_type
sender                    [C4]
recipient                 [C4]
body
media_id
attachment_id
status                    [R19] QUEUED / SENT / DELIVERED / READ / FAILED, plus dropped (§25)
message_status            [R19] Meta pacing status, template sends only
error_code
error_title               [C4]
error_message
error_details             [R19] Meta error_data.details
wa_timestamp
create_date               [C4] "Created At"
```

`<module>.routing.config` (was `whatsapp.routing.config`)

```
account_id
method
fallback_user_id
last_assigned_user_id
```

~~`mode`~~ moved to the account [C1]; ~~`eligible_user_ids`~~ replaced by `routing_enabled` on the operators [C2].

`<module>.webhook.event` [R27] [R35]: raw webhook storage, the only copy of what Meta sent

```
received_at
raw_body                  exact bytes received
state                     new / processed / error
error
```

## 54. Odoo Integration Principle

Where Odoo already has an appropriate model or infrastructure, extend it instead of duplicating it.

Particularly:

* `res.partner`
* CRM lead/opportunity
* `mail.thread`
* `mail.message`
* Discuss
* activities
* attachments
* users/groups
* Odoo bus

Do not create a parallel CRM system.

## 55. API Abstraction

Do not scatter Meta API calls throughout Odoo models.

Create a dedicated service layer.

Example:

```
WhatsApp API Client
├── send_text()
├── send_template()
├── send_image()
├── send_document()
├── send_audio()
├── send_video()
├── get_media()
├── mark_read()
├── get_templates()
└── test_connection()
```

This will make future Meta API changes manageable.

[R23] [R36] [R22] The client maps onto these Meta endpoints:

```
WhatsApp API Client
├── send_message()        POST /{Phone-Number-ID}/messages
│                          (text, media, template, interactive, location,
│                           contacts, reaction; replies via context.message_id;
│                           addressed by phone "to" or BSUID "recipient", §27)
├── mark_read()           POST /{Phone-Number-ID}/messages (status "read",
│                          optional typing_indicator)
├── upload_media()        POST /{Phone-Number-ID}/media
├── get_media_url()       GET  /{Media-ID}  (URL valid 5 minutes)
├── download_media()      GET  <media URL> with the access token
├── get_templates()       GET  /{WABA-ID}/message_templates
├── submit_template()     POST /{WABA-ID}/message_templates
├── get_subscribed_apps() GET  /{WABA-ID}/subscribed_apps
├── subscribe_app()       POST /{WABA-ID}/subscribed_apps
└── test_connection()     GET  /{Phone-Number-ID}?fields=status,… (§34)
```

The API version is the account's configured version [N1].

## 56. Webhook Processor

Create a dedicated processing layer:

```
Webhook
   ↓
Signature validation
   ↓
Event parser
   ↓
Idempotency check
   ↓
Message/status persistence
   ↓
Conversation resolution
   ↓
Routing
   ↓
CRM linking
   ↓
Notification
   ↓
Odoo real-time update
```

[R26] [R27] [R35] [R16] Refined order:

```
Webhook (HTTP request)
   ↓
Signature validation (raw body, §32)        → 400-level if invalid
   ↓
Store raw event (<module>.webhook.event)    → HTTP 200
   ↓  ir.cron._trigger()
Background cron (batches, _commit_progress)
   ↓
Event parser (one POST can hold up to 1000 updates)
   ↓
Idempotency check (message ID, status per message)
   ↓
Identity resolution (BSUID first, §42)
   ↓
Message/status persistence
   ↓
Conversation resolution
   ↓
Routing (Lead Routing only)
   ↓
CRM linking
   ↓
Notification
   ↓
Odoo real-time update
```

## 57. Transaction Safety

A single incoming message must not produce:

* duplicate contact
* duplicate lead
* duplicate conversation
* duplicate message
* duplicate notification

Use database constraints wherever possible.

Recommended unique constraints:

```
(account_id, external_message_id)
```

and appropriate conversation/customer uniqueness rules.

[R29] In Odoo 19 these are declared with `models.Constraint` / `models.UniqueIndex`: `_sql_constraints` is no longer supported (Odoo only logs a warning). `models.UniqueIndex` accepts a partial `WHERE`, which gives:

```
(account_id, external_message_id)                     unique
(wa_account_id, wa_bsuid) WHERE wa_status = 'open'    unique   [R16]
```

If two webhooks for the same new customer are processed at the same moment, the second conversation insert fails on this index; the processor then retries the update and finds the conversation the first one created. The round-robin cursor is advanced under `lock_for_update()` (§16).

## 58. Testing

Developer must provide automated tests.

### Unit tests

* phone normalization
* contact matching
* routing
* round robin
* duplicate webhook
* message parsing
* status parsing
* permissions

### Integration tests

* incoming text
* outgoing text
* image
* document
* template
* delivery status
* read status
* failed message
* reassignment
* closed conversation
* returning customer

### UI tests

* Discuss conversation
* notification
* routed inbox
* assignment
* reassignment
* unread state
* manager view

### Additional tests from the review

* [R26] webhook verification (GET) and signature check (POST) on the raw body; wrong token or signature → 400-level; valid → 200
* [R35] the same webhook delivered twice; one POST holding several updates
* [R16] incoming message with BSUID and no phone number; BSUID change (`user_changed_user_id`, `user_id_update`) re-keys the existing conversation; phone number arriving later
* [R19] status arriving out of order does not go backwards; `failed` status without a `contacts` block
* [R9] regression tests for the Community core paths of §12.1 (member notifications for `whatsapp_message`, fetched tracking for `'whatsapp'` channels)
* [R22] outgoing media rejected when the type or size is not supported
* [R30] a routed conversation's member who is not a manager cannot invite others
* [R31] [D6] changing the lead's salesperson moves the conversation, and starting a conversation from someone else's lead reassigns the lead

## 59. Enterprise Parity Test Suite

This is mandatory.

Create two environments:

```
ENV A = Odoo 19 Enterprise reference
ENV B = Odoo 19 Community + connector
```

Run the same scenario against both.

### Scenario 1

Customer sends:

Hello

Compare:

* conversation created
* location
* operators
* notification
* popup
* unread state

### Scenario 2

Operator replies.

Compare:

* composer
* message rendering
* message status
* customer identity
* notification

### Scenario 3

Customer replies.

Compare:

* notification
* unread
* conversation placement
* message rendering

### Scenario 4

Add user.

Compare:

* button
* dialog
* membership
* resulting notification

### Scenario 5

Send attachment.

Compare:

* attachment rendering
* download/open behavior
* message history

### Scenario 6

Template message.

Compare:

* template selection
* variables
* send behavior
* resulting conversation

### Scenario 7

Conversation inactivity / documented window.

Compare Enterprise behavior exactly.

[R34] Test both windows separately: Meta's 24-hour customer service window (free-form send refused after it closes; template still allowed) and Odoo's 15-day behaviors (scenarios 9 and 10).

### Scenario 8

CRM record.

Compare:

* WhatsApp button
* chatter
* linked customer
* message traceability

[R10] Include the **Send WhatsApp Message** pop-up opened by the button when the model has approved templates.

### Scenario 9 [R5]

Company-initiated conversation: a user sends a template; the customer answers within 15 days.

Compare:

* the Discuss chat window that pops up
* who is in the conversation
* notification

### Scenario 10 [R6]

A user is in the conversation but does not respond for 15 days; the customer then replies.

Compare:

* which users are notified (Enterprise documents that it goes back to all Notify users)

## 60. Acceptance Criteria — No Lead Routing

No Lead Routing is ACCEPTED only if:

* No custom routing occurs.
* No automatic salesperson ownership occurs.
* Configured operators receive the same type of initial notifications as Enterprise.
* Conversations appear in Discuss.
* Customer-initiated conversations behave as group conversations.
* Users can participate in the conversation.
* Users can be added.
* Message history is preserved.
* Attachments work.
* Templates work.
* Delivery/read/failed states work.
* CRM/chatter integration behaves consistently.
* The customer sees only the company’s WhatsApp identity.
* There are no duplicate messages.
* There are no duplicate conversations.
* There are no duplicate CRM contacts.
* [R5] The company-initiated flow behaves as Enterprise (scenario 9).
* [R16] Customers without a visible phone number (BSUID only) are handled without creating duplicate conversations.

## 61. Acceptance Criteria — Lead Routing

Lead Routing is ACCEPTED only if:

* New conversations can be automatically assigned.
* Round robin works.
* Assignment persists across restarts.
* Existing conversations retain ownership.
* Customers do not receive a different WhatsApp number.
* Assigned users can see their conversations.
* Users cannot accidentally send messages from another user’s conversation.
* Managers can view all conversations.
* Managers can reassign conversations.
* Reassignment preserves history.
* New messages appear in real time.
* CRM leads are correctly linked.
* Duplicate leads are prevented.
* Duplicate contacts are prevented.
* Duplicate messages are prevented.
* Unassigned fallback works.
* Notifications go to the correct salesperson.
* [R31] [D6] The conversation owner always equals the lead's salesperson, whichever side is changed.
* [D2] A conversation a salesperson starts with a template is owned by that salesperson.
* [R30] Only managers can add users to a routed conversation.

## 62. Definition of Done

The project is NOT complete when:

“Messages can be sent and received.”

It is complete only when all of the following exist:

### Infrastructure

* Meta Cloud API
* Webhook
* Secure authentication
* Connection testing
* Error handling
* [R16] Business-scoped user ID support

### Messaging

* inbound
* outbound
* text
* media
* templates
* statuses
* retry
* attachments

### Odoo

* Discuss integration
* Contacts
* CRM
* Chatter
* Activities
* Notifications
* permissions

### Routing

* No Lead Routing
* Lead Routing
* round robin
* assignment
* reassignment
* ownership
* unassigned fallback

### Compatibility

* Enterprise reference database
* parity test suite
* documented differences
* zero unresolved P0/P1 parity issues

## 63. Priority Levels

### P0 — Must work

* Incoming WhatsApp
* Outgoing WhatsApp
* Webhook
* authentication
* message persistence
* duplicate prevention
* No Lead Routing
* Enterprise-compatible Discuss behavior
* Lead Routing
* assignment
* reassignment
* [R16] customer identity by business-scoped user ID (Meta: without it, messages from customers who adopt a username cannot be processed)

### P1 — Required

* media
* templates
* delivery/read status
* notifications
* CRM linking
* permissions
* manager inbox
* search
* real-time updates

### P2 — Later

* AI lead qualification
* AI routing
* AI suggested replies
* SLA monitoring
* conversation analytics
* workload balancing
* working-hour routing
* product-based routing
* language-based routing

## 64. Future AI Layer

The architecture must leave room for:

```
WhatsApp
    ↓
AI qualification
    ↓
Extract:
- intent
- product
- budget
- location
- urgency
    ↓
Routing engine
    ↓
Salesperson
```

Do not implement this in MVP unless separately requested.

However, do not architect the MVP in a way that prevents adding it.

## 65. Critical Developer Instruction

Do NOT start coding the UI from this document alone.

First produce a short Enterprise Reference Report containing:

1. Odoo 19 Enterprise version tested
2. Screenshots of each relevant UI
3. Exact user flow for inbound customer message
4. Exact user flow for outbound message
5. Exact notification behavior
6. Exact group/conversation behavior
7. Exact add-user behavior
8. Exact template behavior
9. Exact attachment behavior
10. Exact CRM/chatter behavior
11. Differences between Enterprise and proposed Community implementation
12. Any behavior that cannot legally/technically be reproduced exactly
13. [R33] The technical name of Odoo's Enterprise WhatsApp module (for the install guard, §12.1)
14. [D4] Whether the customer is a member of the Discuss conversation
15. [R16] How the Enterprise reference handles a customer without a visible phone number (the connector's behavior is fixed by §42 regardless)

Only after this report is approved should the custom UI implementation begin.

## 66. Final Product Principle

The connector has two personalities:

### NO LEAD ROUTING

```
"Make Community behave like Odoo Enterprise WhatsApp."
```

The user should not feel that they are using a third-party WhatsApp interface.

### LEAD ROUTING

```
"Turn the same WhatsApp number into a proper sales inbox."
```

The customer experience remains WhatsApp.

The salesperson experience becomes:

```
WhatsApp
     +
Odoo CRM
     +
Assigned ownership
     +
Sales workflow
```

The routing feature must be an additional layer, not a replacement for the underlying WhatsApp/Discuss functionality.

## Appendix A — Revision log and evidence

Every change above is listed here with its source. Line numbers refer to the pinned copies below.

### Sources

| Source | Pinned at |
|---|---|
| Odoo 19 documentation, source of odoo.com/documentation/19.0 (`odoo/documentation`, branch 19.0); `whatsapp.rst` unless another file is named | commit `c4933ae` |
| Odoo 19 Community source (`odoo/odoo`, branch 19.0) | commit `a3f040a` |
| Meta's official OpenAPI specification for the WhatsApp Business Platform (`facebook/openapi`, `business-messaging-api_v23.0.yaml`) | v23.0, latest in the repository |
| Meta's official WhatsApp Node.js SDK (`WhatsApp/WhatsApp-Nodejs-SDK`, archived) | `main` |
| Meta developer documentation on developers.facebook.com: "Service messages" (updated May 21, 2026), "Webhooks" overview, "Create a webhook endpoint", "Business-scoped user IDs" (changelog up to June 29, 2026), "Business phone numbers", "Media" | read 2026-09-30 |

Where the v23.0 OpenAPI specification and the newer developer pages differ, the newer pages were followed (R16, R19).

### Refinements validated against official sources [R#]

| Id | Change | Evidence |
|---|---|---|
| R1 | WhatsApp app menus: Configuration → WhatsApp Business Accounts, Templates, Messages (Failure Type / Failure Reason columns) | Odoo docs L279, L566, L1009 |
| R2 | Enterprise account field labels; Callback URL and Verify Token generated on Test Connection ("Receiving Messages"); Sync Templates ("Sending messages") | Odoo docs L286-316, L336-349, L790-795 |
| R3 | "Operators" are the account's **Notify users** (Control section) | Odoo docs L872-876 |
| R4 | Webhook fields to subscribe; System User token permissions; public https Callback URL | Odoo docs L361-366, L470-492, L513-526; Meta "Webhooks": `whatsapp_business_messaging` for messages, `whatsapp_business_management` for all other webhooks |
| R5 | Company-initiated flow: template sent, answer within 15 days opens a Discuss chat window | Odoo docs L38-40 |
| R6 | Notification rule, including the 15-day return to all Notify users | Odoo docs L877-882 |
| R7 | Reuse Discuss "Invite People" for adding users | `addons/mail/static/src/discuss/core/common/thread_actions.js:77-88`; Odoo docs L889-892 |
| R8 | Conversation is a `discuss.channel` with its own channel type, following `im_livechat` | `addons/im_livechat/models/discuss_channel.py:31,843-847`; `addons/im_livechat/static/src/core/common/thread_model_patch.js:67`; `addons/im_livechat/static/src/core/public_web/discuss_app_category_model_patch.js`, `discuss_app_model_patch.js`; Odoo docs L889-890 |
| R9 | Reuse the keys `whatsapp` / `whatsapp_message` (decision D1); Community core already handles them | `addons/mail/models/discuss/discuss_channel.py:826` and `:1456`; `addons/mail/models/mail_thread.py:4357`; `addons/mail/static/src/core/common/out_of_focus_service.js:41`; `addons/base_automation/static/src/base_automation_actions_one2many_field.xml:27` |
| R10 | Chatter WhatsApp button and Send WhatsApp Message pop-up | Odoo docs `discuss/chatter.rst` L390-393 |
| R11 | WhatsApp touchpoints in other apps, and which apps exist in Community | Odoo docs `discuss/canned_responses.rst` L18-20, `studio/automated_actions.rst` L525-537, `marketing/events/event_setup/create_events.rst` L195-202, `sales/point_of_sale/use.rst` L207-216, `websites/ecommerce/checkout.rst` L290-293, `services/helpdesk/overview/receiving_tickets.rst` L244, `marketing/marketing_automation/understanding_metrics.rst` L43, `finance/accounting/payments/follow_up.rst` L8-36; `addons/helpdesk`, `addons/marketing_automation`, `addons/account_followup` absent from `odoo/odoo` 19.0; `addons/sms/models/ir_actions_server.py:11` |
| R12 | No `mobile` field in Odoo 19; lead field mapping | `addons/crm/models/crm_lead.py:84-99,193-196`; `odoo/addons/base/models/res_partner.py:276` |
| R13 | Connector ships a `utm.source` "WhatsApp" | `addons/utm/data/utm_source_data.xml` (no WhatsApp source) |
| R14 | Search duplicates by phone; CRM's own check uses email/contact only | `addons/crm/models/crm_lead.py:1911-1941`; `addons/phone_validation/models/mail_thread_phone.py` (`phone_mobile_search`) |
| R15 | Normalize with `phone_validation`; `phonenumbers` is optional in Odoo 19, so declare it | `addons/phone_validation/models/models.py:53-110`; `addons/phone_validation/tools/phone_validation.py:112-125`; `requirements.txt` of `odoo/odoo` 19.0 has no `phonenumbers` |
| R16 | Identity by business-scoped user ID first; customers may have no phone; re-key on BSUID change | Meta "Business-scoped user IDs": "BSUID will be assigned to the `user_id` parameter and appear in all messages webhooks, regardless of whether or not the user has enabled the username feature"; "you must support BSUID to avoid losing the ability to process their messages"; "may look like a new user thread to you"; "regenerated if a user changes their phone number"; "Subscribe your apps to this webhook field to be notified of BSUID changes"; "Any changes described in this document are subject to change." OpenAPI v23.0: `from` "Note that a WhatsApp user's phone number and ID may not always match"; system message `user_changed_user_id` with `previous_user_id` |
| R17 | Where the account, number and event type are in the payload | OpenAPI v23.0 `WebhookPayload`, `Entry.id` ("WhatsApp Business Account ID"), `Metadata.phone_number_id`, `Change.field` ("messages: … messages from consumer or status of message sent by business") |
| R18 | Meta's `conversation` object is not a conversation identifier | OpenAPI v23.0 `Statuses.conversation` and `Pricing` (`pricing_model` CBP / PMP); incoming message values carry no `conversation` |
| R19 | Statuses, pacing status, error details, dropped messages, ordering of outgoing messages | Meta "Service messages": "The message_status property is only included in responses when sending a template message that uses a template that is being paced"; "does not indicate successful delivery of your message"; "All messages except authentication templates: 30 days"; "assume the message was dropped"; "the order in which messages are delivered is not guaranteed to match the order of your API requests". Meta "Business-scoped user IDs": the `contacts` block "Will be omitted entirely for `failed` status messages webhooks". OpenAPI v23.0 `Statuses.status` (sent, delivered, read, failed), `StatusError`, `MessageStatus` |
| R20 | Complete incoming message type map; replies; mark as read | OpenAPI v23.0 `IncomingMessage` discriminator; "Send typing indicator and read receipt" example; `MessageContext.message_id`; `addons/mail/models/mail_message_reaction.py`; `addons/mail/models/mail_thread.py:3072-3106` (`parent_id`, `date` accepted) |
| R21 | Click-to-WhatsApp `referral` kept for attribution | OpenAPI v23.0 `ReferralObject` ("Only included if message via a Click to WhatsApp ad") |
| R22 | Media download rules, expiry, supported types and sizes, errors 131052 / 131053 | Meta "Media": "Media URLs **expire after 5 minutes**"; "Media IDs in webhooks expire after 7 days"; "**If you omit your token, the request will fail.**"; supported media tables; "Images must be 8-bit, RGB or RGBA." OpenAPI v23.0 `/{Version}/{Media-ID}` |
| R23 | API client mapped to Meta endpoints | OpenAPI v23.0 paths `/{Phone-Number-ID}/messages`, `/{Phone-Number-ID}/media`, `/{Media-ID}`, `/{WABA-ID}/message_templates`, `/{WABA-ID}/subscribed_apps`, `/{Phone-Number-ID}` |
| R24 | Test Connection checks number status and app subscription; Live-mode warning | Meta "Business phone numbers": must have a status of "connected" in order to send and receive messages via the API; Meta "Webhooks": some webhooks will not be sent if your app is in **Dev** mode; Odoo docs L535-537, L921-937 |
| R25 | Enterprise template features | Odoo docs L568-648, L666-669, L694-764, L790-807, L1002 |
| R26 | Webhook verification and signature; Odoo route settings | Meta "Create a webhook endpoint": "respond with HTTP status 200 and the hub.challenge value"; "HMAC-SHA256 hash, calculated using the post body payload and your app secret as the secret key". Meta SDK `src/api/webhooks.ts:54-58,80-104`, `src/utils.ts` (`generateXHub256Sig`). `odoo/http.py:2488` (CSRF on POST), `odoo/http.py:813-819` (`type='json'` deprecated alias of `jsonrpc`) |
| R27 | Background processing with `ir.cron`; cron workers needed | `odoo/addons/base/models/ir_cron.py:735-762` (`_trigger`), `:846-866` (`_commit_progress`); `odoo/tools/config.py:443` (`--max-cron-threads`, default 2) |
| R28 | Discuss orders and pages messages by ID | `addons/mail/models/mail_message.py:74` (`_order = 'id desc'`), `:947-991` (`_message_fetch`, `limit=30`); `addons/mail/static/src/core/common/thread_model.js:559,711` |
| R29 | Constraints with `models.Constraint` / `models.UniqueIndex`; row lock for the cursor | `odoo/orm/model_classes.py:162-163` (`_sql_constraints` no longer supported); `odoo/orm/table_objects.py:79,125,185`; `addons/mail/models/discuss/discuss_channel_member.py:198`; `odoo/orm/models.py:5577` (`lock_for_update`) |
| R30 | Access and notifications follow conversation membership; admins see all; any member can invite | `addons/mail/security/mail_security.xml` (`ir_rule_discuss_channel_all`, `ir_rule_discuss_channel_group_system`); `odoo/addons/base/models/ir_rule.py` (`_compute_domain`: group rules OR-ed); `addons/mail/models/discuss/discuss_channel.py:882-926` (members as push recipients); `addons/mail/security/mail_security.xml` (`ir_rule_discuss_channel_member_create_is_member_group_user`: members can invite others when the channel type is not `channel` or `chat`); `thread_actions.js:77-88` (invite action offered on every Discuss conversation) |
| R31 | Owner stays equal to the lead's salesperson; CRM auto-assignment and merges | `addons/crm/models/crm_team.py:420` (team assignment of leads without team and salesperson), `:549` (member assignment of leads without salesperson), `:498` (`_allocate_leads_deduplicate`); `addons/crm/models/crm_lead.py` (`_merge_opportunity`, `_merge_dependences`) |
| R32 | "Group chat" is a Discuss conversation, not a WhatsApp group | Odoo docs L42-43; OpenAPI v23.0 `Change.field` (`group_*`), `recipient_type` (individual, group) |
| R33 | Consequences of sharing the keys: never install with Enterprise's WhatsApp app; `ondelete` policies | `odoo/orm/fields_selection.py:39-57,125-170` (`selection_add` merges existing keys; required fields need a policy other than `set null`, `:134-142`); `odoo/addons/base/models/ir_model.py` (`_process_ondelete`); `addons/mail/models/mail_message.py:129` (default `comment`); `addons/mail/models/discuss/discuss_channel.py:73` (default `channel`), `:436-441` (type cannot be changed) |
| R34 | Meta's 24-hour window and Odoo's 15-day behaviors are separate | Meta "Service messages": "a 24-hour timer called a customer service window starts"; "When the window closes, you can only send pre-approved template messages"; "Known issue: In rare cases, you may receive a message from a WhatsApp user but be unable to respond within the customer service window". Odoo docs L38-40, L877-882 |
| R35 | Webhook retries, duplicates, no history API, batches, payload size | Meta "Webhooks": "Meta retries delivery with decreasing frequency until the request succeeds, for up to 7 days"; "These retries can result in duplicate webhook notifications"; "Webhook payloads can be up to 3 MB". Meta "Create a webhook endpoint": "There are no APIs for fetching historical webhook data, so capture and store webhook payloads accordingly"; "POST requests are aggregated and sent in a batch with a maximum of 1000 updates"; "Unacknowledged responses will be dropped after 7 days". `odoo/http.py:247` (128 MiB default limit) |
| R36 | Outgoing addressing: E.164 with "+", or BSUID; error 131062; `messaging_account_id` | Meta "Service messages" and "Business phone numbers": "If the plus sign is omitted, your business phone number's country calling code is prepended"; Meta "Business-scoped user IDs": "`to` (phone number) will take precedence", "Business-scoped User ID (BSUID) recipients are not supported for this message", "sending messages to a BSUID is supported starting in July 2026"; Meta "Service messages": `messaging_account_id` |
| R37 | Subscribe to the `user_id_update` webhook field | Meta "Business-scoped user IDs": "Subscribe your apps to this webhook field to be notified of BSUID changes" |

### Fixes of contradictions inside this spec [C#]

| Id | Change |
|---|---|
| C1 | The routing mode was stored twice (`whatsapp.account.routing_mode` and `whatsapp.routing.config.mode`); it is stored once, on the account. |
| C2 | Routing-eligible users were defined twice (`whatsapp.operator.routing_enabled` and `whatsapp.routing.config.eligible_user_ids`); there is one list, the Notify users, with a routing flag. |
| C3 | §20 listed conversation fields missing from §53 (customer, business number, team, created at); reconciled in §53.1. |
| C4 | §25 listed message fields missing from §53 (sender, recipient, error title, created at); reconciled in §53.1. |
| C5 | §3 showed Lead Routing leaving Discuss, against §12, §54 and §66; both modes now end in the same Discuss conversation, and the routed inbox is a filtered entry point into it. |

### Decisions by the product owner [D#]

| Id | Decision |
|---|---|
| D1 | Reuse the Enterprise selection keys `whatsapp`, `whatsapp_message` and the `whatsapp` action type (§12.1). |
| D2 | In Lead Routing, a conversation started by a salesperson with a template is owned by that salesperson; no round-robin. |
| D6 | If that salesperson is not the lead's salesperson, the lead is reassigned to them; the owner always equals the lead's salesperson. |

### Design notes that avoid depending on unverified facts [N#]

| Id | Note |
|---|---|
| N1 | The Graph API version is set per account by the administrator; no "current version" is hard-coded. |
| N2 | Template statuses are stored exactly as Meta sends them; known values get labels, others are shown as received. |

## Appendix B — Open decisions

| Id | Question | Where it will be answered |
|---|---|---|
| D3 | What happens to existing conversations when an account switches between No Lead Routing and Lead Routing? | To be defined before the Lead Routing implementation starts. |
| D4 | Is the customer a member of the Discuss conversation in Enterprise? | Enterprise Reference Report (§65, item 14). |
| D5 | On uninstall, are WhatsApp conversations deleted (`'cascade'`, as Live Chat does) or turned into private `'group'` conversations? And what is the procedure for moving to Odoo's Enterprise WhatsApp app later? | Before the first production install. |
