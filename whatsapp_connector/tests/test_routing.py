import json
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, LockError, UserError
from odoo.tests import tagged

from . import payloads
from .test_outbound import OutboundCase, sent

NEW_BSUID = "KE.77001122334455667788"
NEW_WA_ID = "254712345678"


def now_ts():
    return str(int(fields.Datetime.now().timestamp()))


class RoutingCase(OutboundCase):
    """Lead Routing (Mode B) on the test account: Andrew, Sarah, Brian in round-robin order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account.routing_mode = "lead"
        cls.config = cls.account._wa_routing_config()

    def _process(self, payload, tracking=False):
        event = self.env["whatsapp_connector.webhook.event"].create({"raw_body": json.dumps(payload)})
        # the test class disables tracking, and with it CRM's assignment notices
        event.with_context(tracking_disable=not tracking)._process()
        self.assertEqual(event.state, "processed", event.error)
        return event

    def _inbound(self, msg_id, body="Hello", bsuid=NEW_BSUID, wa_id=NEW_WA_ID, name="John Kamau",
                 tracking=False, **extra):
        self._process(payloads.inbound(
            [payloads.text_message(msg_id, body, timestamp=now_ts(), wa_id=wa_id, bsuid=bsuid, **extra)],
            contacts=[payloads.contact(name=name, wa_id=wa_id, bsuid=bsuid)],
        ), tracking=tracking)
        return self.env["discuss.channel"].search([
            ("channel_type", "=", "whatsapp"), ("wa_bsuid", "=", bsuid),
        ], order="id desc", limit=1)

    def _operator(self, user):
        return self.account.operator_ids.filtered(lambda op: op.user_id == user)

    def _audit(self, channel):
        return channel.message_ids.filtered(lambda m: m.message_type == "notification").mapped("body")


@tagged("post_install", "-at_install")
class TestRoundRobin(RoutingCase):
    """§16, §17, §41, R29."""

    def test_cycle_and_skips(self):
        users = [self.config._wa_next_user() for _i in range(4)]
        self.assertEqual(users, [self.user_a, self.user_b, self.user_c, self.user_a])
        self.assertEqual(self.config.last_assigned_user_id, self.user_a, "the cursor is stored")
        # B disabled: A → C → A → C
        self._operator(self.user_b).routing_enabled = False
        self.assertEqual([self.config._wa_next_user() for _i in range(3)], [self.user_c, self.user_a, self.user_c])
        # an archived user and a user without WhatsApp access are skipped too
        self.user_c.active = False
        self.user_a.group_ids = [Command.unlink(self.env.ref("whatsapp_connector.group_whatsapp_user").id)]
        self._operator(self.user_b).routing_enabled = True
        self.assertEqual(self.config._wa_next_user(), self.user_b)

    def test_fallback_then_nobody(self):
        self.account.operator_ids.routing_enabled = False
        self.config.fallback_user_id = self.manager
        self.assertEqual(self.config._wa_next_user(), self.manager)
        self.config.fallback_user_id = False
        self.assertFalse(self.config._wa_next_user())

    def test_settings_on_account_without_configuration(self):
        """The routing settings are related fields: they need the configuration row to exist."""
        self.config.unlink()
        self.account.invalidate_recordset(["routing_config_id"])
        self.assertFalse(self.account.routing_config_id)
        self.account.write({"routing_fallback_user_id": self.manager.id})
        self.assertEqual(self.account.routing_config_id.fallback_user_id, self.manager)

    def test_held_cursor_is_retried(self):
        """R29: another transaction holds the cursor: the event is processed again later."""
        Config = type(self.config)
        event = self.env["whatsapp_connector.webhook.event"].create({"raw_body": json.dumps(payloads.inbound(
            [payloads.text_message("wamid.l", "Hi", timestamp=now_ts(), wa_id=NEW_WA_ID, bsuid=NEW_BSUID)],
            contacts=[payloads.contact(name="John", wa_id=NEW_WA_ID, bsuid=NEW_BSUID)],
        ))})
        with patch.object(Config, "lock_for_update", side_effect=LockError("locked")), \
                self.assertRaises(LockError):
            event._process()
        self.assertEqual(event.state, "new")


@tagged("post_install", "-at_install")
class TestInboundRouting(RoutingCase):
    """§15, §22, §40, §43, §44, R21, Q2."""

    def test_new_customer_gets_owner_and_lead(self):
        channel = self._inbound("wamid.1", referral={"source_type": "ad", "headline": "Sofas"}, tracking=True)
        self.assertRecordValues(channel, [{
            "wa_routed": True, "wa_assigned_user_id": self.user_a.id, "wa_status": "open",
        }])
        self.assertEqual(channel.channel_member_ids.partner_id, self.user_a.partner_id, "the owner only (§21)")
        lead = channel.wa_lead_id
        self.assertRecordValues(lead, [{
            "name": "WhatsApp — John Kamau", "user_id": self.user_a.id, "wa_bsuid": NEW_BSUID,
            "phone": "+254712345678",
            "source_id": self.env.ref("whatsapp_connector.utm_source_whatsapp").id,
        }])
        self.assertEqual(lead.wa_referral, {"source_type": "ad", "headline": "Sofas"}, "R21")
        self.assertEqual(channel.wa_assigned_team_id, lead.team_id)
        self.assertTrue(any("round-robin" in body for body in self._audit(channel)))
        # CRM's own assignment notification (§46), a user_notification, which
        # mail.thread.message_ids leaves out
        notice = self.env["mail.message"].search([
            ("model", "=", "crm.lead"), ("res_id", "=", lead.id), ("message_type", "=", "user_notification"),
        ])
        self.assertIn(self.user_a.partner_id, notice.partner_ids)

    def test_existing_lead_keeps_its_salesperson(self):
        """R31: a customer with an open lead goes to its salesperson, without round-robin."""
        lead = self.env["crm.lead"].create({"name": "Dining table", "phone": "+254712345678", "user_id": self.user_c.id})
        channel = self._inbound("wamid.1")
        self.assertEqual(channel.wa_lead_id, lead)
        self.assertEqual(channel.wa_assigned_user_id, self.user_c)
        self.assertFalse(self.config.last_assigned_user_id, "no round-robin turn used")

    def test_returning_customer_keeps_owner(self):
        """§22: a later message never routes again."""
        first = self._inbound("wamid.1")
        second = self._inbound("wamid.2", body="Thursday: still interested")
        self.assertEqual(first, second)
        self.assertEqual(second.wa_assigned_user_id, self.user_a)
        self.assertEqual(self.config.last_assigned_user_id, self.user_a)
        self.assertEqual(self.env["crm.lead"].search_count([("wa_bsuid", "=", NEW_BSUID)]), 1)

    def test_closed_conversation_reopens_with_owner(self):
        """Q2: same thread, same owner, same open lead."""
        channel = self._inbound("wamid.1")
        lead = channel.wa_lead_id
        channel.with_user(self.user_a).action_wa_close()
        self.assertEqual(channel.wa_status, "closed")
        again = self._inbound("wamid.2")
        self.assertEqual(again, channel)
        self.assertRecordValues(channel, [{"wa_status": "open", "wa_assigned_user_id": self.user_a.id}])
        self.assertEqual(channel.wa_lead_id, lead)

    def test_reopened_with_ineligible_owner_routes_again(self):
        channel = self._inbound("wamid.1")
        channel.with_user(self.user_a).action_wa_close()
        self._operator(self.user_a).routing_enabled = False
        self._inbound("wamid.2")
        self.assertEqual(channel.wa_assigned_user_id, self.user_b)
        self.assertEqual(channel.wa_lead_id.user_id, self.user_b, "the lead follows (R31)")
        self.assertEqual(channel.channel_member_ids.partner_id, self.user_b.partner_id)
        self.assertIn("Reassigned from Andrew to Sarah (round-robin).", " ".join(self._audit(channel)))

    def test_reopened_after_lost_lead_gets_new_lead(self):
        channel = self._inbound("wamid.1")
        old_lead = channel.wa_lead_id
        channel.with_user(self.user_a).action_wa_close()
        old_lead.action_set_lost()
        self._inbound("wamid.2")
        self.assertNotEqual(channel.wa_lead_id, old_lead)
        self.assertEqual(channel.wa_lead_id.user_id, self.user_a)

    def test_unassigned_notifies_managers(self):
        """§40: never silently lost."""
        self.account.operator_ids.routing_enabled = False
        channel = self._inbound("wamid.1")
        self.assertFalse(channel.wa_assigned_user_id)
        self.assertFalse(channel.channel_member_ids)
        self.assertFalse(channel.wa_lead_id.user_id)
        self.assertTrue(any("Waiting for a salesperson" in body for body in self._audit(channel)))
        notice = channel.wa_lead_id.message_ids.filtered(lambda m: "waiting for a salesperson" in (m.body or ""))
        self.assertIn(self.manager.partner_id, notice.partner_ids)

    def test_mode_a_conversation_stays_mode_a(self):
        """Switching the account to Lead Routing leaves existing conversations as they are."""
        self.account.routing_mode = "none"
        channel = self._inbound("wamid.1")
        self.assertFalse(channel.wa_routed)
        self.account.routing_mode = "lead"
        self._inbound("wamid.2")
        self.assertFalse(channel.wa_routed)
        self.assertFalse(channel.wa_assigned_user_id)


@tagged("post_install", "-at_install")
class TestOwnership(RoutingCase):
    """§18, §21, §23, R30, R31, Q3."""

    def test_manager_reassigns(self):
        channel = self._inbound("wamid.1")
        channel.with_user(self.manager).write({"wa_assigned_user_id": self.user_b.id})
        self.assertEqual(channel.wa_lead_id.user_id, self.user_b)
        self.assertEqual(channel.channel_member_ids.partner_id, self.user_b.partner_id, "Andrew loses access")
        self.assertTrue(any("Reassigned from Andrew to Sarah by Manager" in body for body in self._audit(channel)))
        with self.assertRaises(AccessError):
            channel.with_user(self.user_b).write({"wa_assigned_user_id": self.user_c.id})

    def test_lead_salesperson_moves_conversation(self):
        channel = self._inbound("wamid.1")
        lead = channel.wa_lead_id
        lead.with_user(self.manager).user_id = self.user_c
        self.assertEqual(channel.wa_assigned_user_id, self.user_c)
        self.assertEqual(channel.channel_member_ids.partner_id, self.user_c.partner_id)
        # CRM's own assignment writes user_id the same way
        lead._handle_salesmen_assignment(user_ids=self.user_b.ids, team_id=lead.team_id.id)
        self.assertEqual(channel.wa_assigned_user_id, self.user_b)
        audit = " ".join(self._audit(channel))
        self.assertIn("Reassigned from Andrew to Brian, the lead's new salesperson.", audit)
        self.assertIn("Reassigned from Brian to Sarah, the lead's new salesperson.", audit)
        # no salesperson on the lead: the conversation waits too (§40)
        lead.user_id = False
        self.assertFalse(channel.wa_assigned_user_id)
        self.assertIn("Unassigned from Sarah: waiting for a salesperson.", " ".join(self._audit(channel)))

    def test_merge_follows_surviving_lead(self):
        channel = self._inbound("wamid.1")
        other = self.env["crm.lead"].create({
            "name": "Sofa", "type": "opportunity", "probability": 80, "user_id": self.user_c.id,
            "partner_id": channel.wa_partner_id.id,
        })
        head = (channel.wa_lead_id | other)._merge_opportunity(auto_unlink=True, max_length=0)
        self.assertEqual(channel.wa_lead_id, head)
        self.assertEqual(channel.wa_assigned_user_id, head.user_id)

    def test_invite_restricted_to_managers(self):
        channel = self._inbound("wamid.1")
        with self.assertRaises(AccessError):
            channel.with_user(self.user_a).add_members(partner_ids=self.user_b.partner_id.ids)
        channel.with_user(self.manager).add_members(partner_ids=self.user_b.partner_id.ids)
        self.assertIn(self.user_b.partner_id, channel.channel_member_ids.partner_id)

    def test_only_members_write(self):
        channel = self._inbound("wamid.1")
        with self.assertRaisesRegex(UserError, "assigned to Andrew"):
            self._post(channel, self.manager, "Hi from the manager")
        self._post(channel, self.user_a, "Hi from Andrew")

    def test_take_unassigned(self):
        """Q3: eligible salespeople see and take unassigned conversations; managers too."""
        self.account.operator_ids.routing_enabled = False
        channel = self._inbound("wamid.1")
        self._operator(self.user_b).routing_enabled = True
        Channel = self.env["discuss.channel"]
        self.assertEqual(Channel.with_user(self.user_b).search([("id", "=", channel.id)]), channel)
        self.assertFalse(Channel.with_user(self.user_c).search([("id", "=", channel.id)]), "not in routing")
        channel.with_user(self.user_b).action_wa_take()
        self.assertEqual(channel.wa_assigned_user_id, self.user_b)
        self.assertEqual(channel.wa_lead_id.user_id, self.user_b)
        self._operator(self.user_a).routing_enabled = True
        with self.assertRaises(UserError):  # AccessError is a UserError
            channel.with_user(self.user_a).action_wa_take()
        channel.with_user(self.manager).action_wa_take()  # "Assign to me"
        self.assertEqual(channel.wa_assigned_user_id, self.manager)
        audit = " ".join(self._audit(channel))
        self.assertIn("Sarah took the conversation.", audit)
        self.assertIn("Reassigned from Sarah to Manager by Manager.", audit)

    def test_close_reopen_permissions(self):
        channel = self._inbound("wamid.1")
        # the form shows Close and Reopen to the owner and managers only
        self.assertEqual(
            [channel.with_user(user).wa_user_can_close for user in (self.user_a, self.user_b, self.manager)],
            [True, False, True],
        )
        with self.assertRaises(AccessError):
            channel.with_user(self.user_b).action_wa_close()
        channel.with_user(self.user_a).action_wa_close()
        channel.with_user(self.manager).action_wa_reopen()
        self.assertEqual(channel.wa_status, "open")

    def test_reopen_refused_while_another_is_open(self):
        """The check sees every conversation, not only the ones the owner can read."""
        channel = self._inbound("wamid.1")
        channel.with_user(self.user_a).action_wa_close()
        self.env.flush_all()  # the unique index sees the closed status
        other = self.env["discuss.channel"]._wa_create_conversation(
            self.account, channel.wa_partner_id, {"bsuid": NEW_BSUID, "wa_id": NEW_WA_ID},
            members=self.user_b, routed=True,
        )
        other._wa_assign(self.user_b, reason="manager")
        with self.assertRaisesRegex(UserError, "already has an open conversation"):
            channel.with_user(self.user_a).action_wa_reopen()


@tagged("post_install", "-at_install")
class TestTemplateRouting(RoutingCase):
    """D2, D6, Q1."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_template = cls.env["whatsapp_connector.template"].create({
            "name": "Hello", "template_name": "hello", "account_id": cls.account.id, "status": "APPROVED",
            "model_id": cls.env["ir.model"]._get_id("res.partner"), "body": "Hello!",
        })
        cls.lead_template = cls.env["whatsapp_connector.template"].create({
            "name": "Quote", "template_name": "quote", "account_id": cls.account.id, "status": "APPROVED",
            "model_id": cls.env["ir.model"]._get_id("crm.lead"), "body": "Your quote is ready.",
        })

    def test_sender_owns_new_conversation(self):
        """D2: no round-robin for a conversation the salesperson starts."""
        wa = self.partner_template.with_user(self.user_b)._wa_send_to_record(self.customer)
        channel = wa.channel_id
        self.assertRecordValues(channel, [{"wa_routed": True, "wa_assigned_user_id": self.user_b.id}])
        self.assertEqual(channel.channel_member_ids.partner_id, self.user_b.partner_id)
        self.assertEqual(channel.wa_lead_id.user_id, self.user_b)
        self.assertFalse(self.config.last_assigned_user_id)

    def test_template_from_lead_reassigns_it(self):
        """D6: the lead goes to the sender; its previous salesperson is told."""
        lead = self.env["crm.lead"].create({"name": "Quote 7", "partner_id": self.customer.id, "user_id": self.user_c.id})
        wa = self.lead_template.with_user(self.user_b)._wa_send_to_record(lead)
        self.assertEqual(lead.user_id, self.user_b)
        self.assertEqual(wa.channel_id.wa_assigned_user_id, self.user_b)
        self.assertEqual(wa.channel_id.wa_lead_id, lead)
        notice = lead.message_ids.filtered(lambda m: "who sent a WhatsApp template" in (m.body or ""))
        self.assertIn(self.user_c.partner_id, notice.partner_ids)

    def test_existing_owner_kept(self):
        """Q1: another salesperson's conversation stays theirs; the template still goes out."""
        channel = self._inbound("wamid.1", bsuid=self.customer.wa_bsuid, wa_id="16505551234", name="Sheena Nelson")
        self.assertEqual(channel.wa_assigned_user_id, self.user_a)
        wa = self.partner_template.with_user(self.user_b)._wa_send_to_record(self.customer)
        self.assertEqual(wa.channel_id, channel)
        self.assertEqual(channel.wa_assigned_user_id, self.user_a)
        self.assertNotIn(self.user_b.partner_id, channel.channel_member_ids.partner_id)
        self.assertEqual(wa.mail_message_id.author_id, self.user_b.partner_id)
        send = self._send_queued(answer=sent("wamid.t"))
        self.assertEqual(send.call_args.args[0]["type"], "template")


@tagged("post_install", "-at_install")
class TestInbox(RoutingCase):
    """§19, §45."""

    def test_filters_preview_unread(self):
        channel = self._inbound("wamid.1", body="Do you deliver to Nairobi on weekends?")
        Channel = self.env["discuss.channel"].with_user(self.user_a)
        mine = ["|", ("wa_assigned_user_id", "=", self.user_a.id),
                "&", ("wa_routed", "=", False), ("is_member", "=", True)]
        self.assertEqual(Channel.search([("channel_type", "=", "whatsapp")] + mine), channel)
        self.assertEqual(channel.with_user(self.user_a).wa_last_message_preview, "Do you deliver to Nairobi on weekends?")
        self.assertEqual(Channel.search([("channel_type", "=", "whatsapp"), ("wa_unread_count", ">", 0)]), channel)
        self.assertEqual(channel.with_user(self.user_a).wa_unread_count, 1)
        self.assertEqual(channel.with_user(self.manager).wa_unread_count, 0, "each user's own count")
        self.assertEqual(channel.wa_customer_contact, "+254712345678")
