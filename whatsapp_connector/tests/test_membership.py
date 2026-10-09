from odoo.tests import tagged

from .common import WhatsappCase


@tagged("post_install", "-at_install")
class TestManagerJoins(WhatsappCase):
    """Discuss makes whoever types in a conversation a member of it.

    For a WhatsApp manager who was not one yet, mail's member rules refused that
    with an AccessError ("doesn't have 'create' access to Channel Member"),
    unless the manager was also a system administrator.
    """

    def _join(self, channel, user):
        return channel.with_user(user)._find_or_create_member_for_self()

    def test_manager_joins_unrouted_conversation(self):
        channel = self._make_channel(self.user_a.partner_id)
        self.assertFalse(self.manager.has_group("base.group_system"))
        member = self._join(channel, self.manager)
        self.assertEqual(member.partner_id, self.manager.partner_id)
        self.assertIn(self.manager.partner_id, channel.channel_member_ids.partner_id)
        # a second keystroke finds the same member
        self.assertEqual(self._join(channel, self.manager), member)
        self.assertFalse(
            channel.message_ids.filtered(lambda m: "joined" in (m.body or "")),
            "joining is not posted in the customer's conversation",
        )

    def test_manager_not_added_to_routed_conversation(self):
        channel = self._make_channel(
            self.user_a.partner_id, wa_routed=True, wa_assigned_user_id=self.user_a.id,
        )
        self.assertFalse(self._join(channel, self.manager))
        self.assertNotIn(self.manager.partner_id, channel.channel_member_ids.partner_id)

    def test_member_keeps_own_member(self):
        channel = self._make_channel(self.user_a.partner_id)
        member = self._join(channel, self.user_a)
        self.assertEqual(member, channel.channel_member_ids.filtered(
            lambda m: m.partner_id == self.user_a.partner_id))

    def test_plain_user_still_cannot_join(self):
        """A WhatsApp user sees only their own conversations: nothing changes for them."""
        channel = self._make_channel(self.user_a.partner_id)
        with self.assertRaises(Exception):
            self._join(channel, self.user_b)
        self.assertNotIn(self.user_b.partner_id, channel.channel_member_ids.partner_id)
