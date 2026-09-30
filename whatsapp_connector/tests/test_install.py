import psycopg2

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.whatsapp_connector.hooks import pre_init_hook

from .common import WhatsappCase


@tagged("post_install", "-at_install")
class TestInstall(WhatsappCase):

    def test_selection_keys(self):
        """The three keys shared with Odoo Enterprise's WhatsApp app exist (R9)."""
        channel_types = dict(self.env["discuss.channel"]._fields["channel_type"].selection)
        message_types = dict(self.env["mail.message"]._fields["message_type"].selection)
        self.assertIn("whatsapp", channel_types)
        self.assertIn("whatsapp_message", message_types)

    def test_lead_source(self):
        source = self.env.ref("whatsapp_connector.utm_source_whatsapp")
        self.assertEqual(source.name, "WhatsApp")

    def test_notify_users_and_callback(self):
        self.assertEqual(self.account.notify_user_ids, self.user_a | self.user_b | self.user_c)
        self.account.operator_ids.filtered(lambda op: op.user_id == self.user_c).active = False
        self.assertEqual(self.account.notify_user_ids, self.user_a | self.user_b)
        self.assertTrue(self.account.callback_url.endswith("/whatsapp_connector/webhook"))

    def test_credentials_hidden_from_managers(self):
        """Meta credentials are readable by WhatsApp Administrators only (§32)."""
        account = self.account.with_user(self.manager)
        self.assertEqual(account.name, "Sales")
        with self.assertRaises(AccessError):
            account.access_token  # noqa: B018
        with self.assertRaises(AccessError):
            account.app_secret  # noqa: B018

    def test_one_open_conversation_per_customer(self):
        """Constraint from §57 / R29: one open conversation per account and customer."""
        self._make_channel(self.user_a.partner_id)
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.errors.UniqueViolation):
            with self.env.cr.savepoint():
                self._make_channel(self.user_b.partner_id)
        # a closed conversation does not block a new one
        self.env["discuss.channel"].search([("wa_customer_key", "!=", False)]).wa_status = "closed"
        self.env.flush_all()
        self._make_channel(self.user_b.partner_id)

    def test_message_id_unique_per_account(self):
        Message = self.env["whatsapp_connector.message"]
        vals = {"account_id": self.account.id, "direction": "inbound", "status": "received",
                "external_message_id": "wamid.X"}
        Message.create(vals)
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.errors.UniqueViolation):
            with self.env.cr.savepoint():
                Message.create(vals)

    def test_status_never_moves_backwards(self):
        """R19: a late 'delivered' after 'read' is ignored; 'failed' only before delivery."""
        message = self.env["whatsapp_connector.message"].create({
            "account_id": self.account.id, "direction": "outbound", "status": "queued",
        })
        message._apply_status("sent")
        message._apply_status("read")
        message._apply_status("delivered")
        self.assertEqual(message.status, "read")
        message._apply_status("failed", errors=[{"code": 131047, "title": "Re-engagement message"}])
        self.assertEqual(message.status, "read")

        failing = self.env["whatsapp_connector.message"].create({
            "account_id": self.account.id, "direction": "outbound", "status": "sent",
        })
        failing._apply_status("failed", errors=[{
            "code": 131026, "title": "Message undeliverable", "message": "Undeliverable",
            "error_data": {"details": "Recipient not on WhatsApp"},
        }])
        self.assertEqual(failing.status, "failed")
        self.assertEqual(failing.error_code, "131026")
        self.assertEqual(failing.error_title, "Message undeliverable")
        self.assertEqual(failing.error_details, "Recipient not on WhatsApp")

    def test_template_status_shown_as_received(self):
        """N2: known statuses get a label; anything else is shown as Meta sent it."""
        template = self.env["whatsapp_connector.template"].create({
            "name": "Hello", "template_name": "hello", "account_id": self.account.id,
            "body": "Hello {{1}}",
        })
        template.status = "APPROVED"
        self.assertEqual(template.status_label, "Approved")
        template.status = "PAUSED"
        self.assertEqual(template.status_label, "Paused")

    def test_install_guard(self):
        """R33: refuse to install next to Odoo Enterprise's WhatsApp app."""
        pre_init_hook(self.env)  # nothing to refuse
        self.env["ir.module.module"].create({"name": "whatsapp", "state": "installed"})
        with self.assertRaises(UserError):
            pre_init_hook(self.env)


@tagged("post_install", "-at_install")
class TestAccess(WhatsappCase):
    """A WhatsApp conversation is readable by its members and by WhatsApp managers (R30)."""

    def test_members_and_managers_only(self):
        channel = self._make_channel(self.user_a.partner_id)
        self.assertTrue(channel.with_user(self.user_a).name)
        with self.assertRaises(AccessError):
            channel.with_user(self.user_b).check_access("read")
        with self.assertRaises(AccessError):
            channel.with_user(self.outsider).check_access("read")
        channel.with_user(self.manager).check_access("read")
        members = channel.with_user(self.manager).channel_member_ids
        self.assertIn(self.user_a.partner_id, members.partner_id)


@tagged("post_install", "-at_install")
class TestCommunityCorePaths(WhatsappCase):
    """Odoo 19 Community core already handles the reused keys (R9); these must keep working."""

    def test_members_get_push_recipients(self):
        channel = self._make_channel(self.user_a.partner_id | self.user_b.partner_id)
        message = channel.with_user(self.user_a).message_post(body="Hi", message_type="whatsapp_message")
        recipients = channel._notify_get_recipients(message, msg_vals={
            "message_type": "whatsapp_message", "author_id": self.user_a.partner_id.id,
            "partner_ids": [],
        })
        pushed = {r["id"] for r in recipients if r["notif"] == "web_push"}
        self.assertIn(self.user_b.partner_id.id, pushed)
        self.assertNotIn(self.user_a.partner_id.id, pushed)
        # the same call for a type core does not handle returns nobody
        self.assertEqual(channel._notify_get_recipients(message, msg_vals={
            "message_type": "notification", "author_id": self.user_a.partner_id.id, "partner_ids": [],
        }), [])

    def test_extra_notifications(self):
        channel = self._make_channel(self.user_a.partner_id | self.user_b.partner_id)
        message = channel.with_user(self.user_a).message_post(body="Hi", message_type="whatsapp_message")
        recipients_data = [{"active": True, "id": self.user_b.partner_id.id, "notif": "inbox"}]
        extra = channel._notify_get_recipients_for_extra_notifications(message, recipients_data, msg_vals={
            "message_type": "whatsapp_message", "author_id": self.user_a.partner_id.id,
        })
        self.assertEqual(set(extra), {self.user_b.partner_id.id})

    def test_channel_fetched(self):
        channel = self._make_channel(self.user_a.partner_id | self.user_b.partner_id)
        message = channel.with_user(self.user_a).message_post(body="Hi", message_type="whatsapp_message")
        channel.with_user(self.user_b).channel_fetched()
        member_b = channel.channel_member_ids.filtered(lambda m: m.partner_id == self.user_b.partner_id)
        member_b.invalidate_recordset(["fetched_message_id"])
        self.assertEqual(member_b.fetched_message_id, message)
