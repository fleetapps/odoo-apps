# Odoo 19 WhatsApp Connector

**Enterprise-Compatible Mode + Optional Lead Routing**

- Document status: Development specification
- Target: Odoo 19 Community
- Integration: Meta WhatsApp Business Platform / Cloud API
- Primary use case: One company WhatsApp number → multiple Odoo users → shared or individually assigned customer conversations
- Initial team: 3 Odoo users
- Priority: Production-grade implementation; Enterprise behavioral compatibility is mandatory

## 1. Executive Summary

Build an Odoo 19 WhatsApp connector that integrates a company’s WhatsApp Business Platform number into Odoo.

The connector MUST support two operating modes:

### Mode A — No Lead Routing

This mode MUST behave like the Odoo 19 Enterprise WhatsApp experience for WhatsApp conversations.

It must NOT introduce a custom sales inbox, ownership model, lead assignment, round-robin allocation, or salesperson-specific routing.

Customer-initiated conversations must behave as Odoo Enterprise does:

Customer → company WhatsApp number → Odoo Discuss → WhatsApp conversation/group chat involving the configured WhatsApp channel operators.

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
               ▼                ▼
         Odoo Discuss      Routing Engine
                                  │
                         ┌────────┼────────┐
                         ▼        ▼        ▼
                       User 1   User 2   User 3
```

## 4. Settings

Create a WhatsApp configuration area.

Example:

Settings → WhatsApp → WhatsApp Business Accounts

The account configuration must contain:

### Connection

* WhatsApp Business Account ID
* Phone Number ID
* Access Token
* App ID
* App Secret
* Webhook Verify Token
* API version
* Company
* Phone number
* Display name
* Connection status

### Operators

A list of Odoo users who are responsible for the WhatsApp channel.

Example:

```
WhatsApp Business Account
Company: Example Ltd
Phone: +254 XXX XXX XXX

Operators:
 Andrew
 Sarah
 Brian
```

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

Use the standard Odoo WhatsApp conversation model. Incoming customer conversations are handled through Discuss by the configured WhatsApp operators.

**Lead Routing**

Automatically assign incoming WhatsApp conversations to individual Odoo users and link them to CRM leads.

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

The custom connector must then reproduce the observable Enterprise behavior.

If there is any disagreement between this document and an actual Odoo 19 Enterprise reference instance, the Enterprise instance is the behavioral reference for Mode A unless explicitly overridden in this specification.

## 8. No Lead Routing — Incoming Message

When:

```
Customer → WhatsApp Business Number
```

the connector receives the Meta webhook.

The system must:

1. Validate the webhook.
2. Identify the WhatsApp Business Account.
3. Identify the phone number.
4. Identify the WhatsApp customer.
5. Match the customer to an Odoo contact where possible.
6. Create the appropriate conversation/thread.
7. Make the conversation available through Odoo Discuss.
8. Notify the configured WhatsApp operators according to the Enterprise behavior.
9. Preserve the WhatsApp message ID.
10. Prevent duplicate message creation.

The conversation must NOT automatically become:

```
CRM Lead
    ↓
Assigned salesperson
```

unless the customer was already associated with a CRM record through existing Odoo behavior.

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

## 15. Lead Routing — CRM Lead Creation

For a new WhatsApp customer/conversation:

1. Identify WhatsApp phone number.
2. Search Odoo contacts.
3. Search existing CRM leads/opportunities associated with that phone number.
4. If an appropriate existing record exists, link the conversation.
5. Otherwise create a CRM Lead.

Minimum lead fields:

```
Lead Name
Contact Name
Phone
Mobile
Email, if available
Source = WhatsApp
WhatsApp Conversation
Assigned User
Sales Team
Created Date
Last WhatsApp Message
```

Recommended lead name:

```
WhatsApp — <Customer Name>
```

If customer name is unavailable:

```
WhatsApp — +254XXXXXXXXX
```

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

## 21. Ownership Rule

There must be only one primary owner.

```
Conversation
    ↓
Primary owner = User A
```

Additional users can optionally be given access by an administrator/manager.

Do not turn a routed conversation into an unrestricted group chat by default.

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

## 28. WhatsApp Conversation Window

The implementation must respect the actual Meta/WhatsApp messaging rules.

The developer must NOT hard-code assumptions about conversation windows without verifying the current Meta API behavior.

Odoo’s current documentation describes its own WhatsApp conversation behavior around a 15-day response period. This must be tested against the exact Odoo 19 Enterprise reference implementation and the current Meta API behavior.

Where Meta requires a template for an outbound message, the UI must prevent/handle an invalid free-form outbound send rather than silently failing.

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

## 42. Existing Customer Logic

Customer identity matching should prioritize:

1. WhatsApp phone number
2. normalized mobile number
3. existing WhatsApp conversation
4. linked Odoo contact
5. linked CRM lead/opportunity

Phone numbers must be normalized to E.164 where possible.

Example:

```
0712 345 678
+254 712 345 678
254712345678
```

should resolve to the same normalized number where country context makes this unambiguous.

## 43. New vs Existing Conversation

Define:

### New conversation

No existing open/active WhatsApp conversation exists for the customer.

### Existing conversation

A previous conversation exists and remains associated with the customer.

Routing occurs only when appropriate for a new conversation.

Existing conversation ownership is preserved.

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

## 47. Search

Search conversations by:

* customer name
* phone number
* lead name
* message content
* assigned user

Phone-number search is particularly important.

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

## 51. Media

Incoming media must:

1. Be received from Meta.
2. Be downloaded securely.
3. Be attached to the corresponding Odoo message.
4. Preserve MIME type.
5. Preserve filename where available.
6. Not expose Meta media URLs directly to end users if temporary/unsafe.

Outbound media must use the correct WhatsApp API flow.

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

## 53. Data Model — Minimum

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

### Scenario 8

CRM record.

Compare:

* WhatsApp button
* chatter
* linked customer
* message traceability

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
