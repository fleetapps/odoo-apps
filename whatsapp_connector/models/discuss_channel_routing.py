from odoo import api, fields, models
from odoo.exceptions import AccessError, LockError, UserError
from odoo.tools import SQL, html2plaintext

from odoo.addons.mail.tools.discuss import Store
from odoo.addons.whatsapp_connector.models.discuss_channel import is_whatsapp_channel

MANAGER_GROUP = "whatsapp_connector.group_whatsapp_manager"
PREVIEW_LENGTH = 80


def is_routed(channel):
    return channel.channel_type == "whatsapp" and channel.wa_routed


class DiscussChannel(models.Model):
    """Lead Routing (Mode B): one owner per conversation, kept equal to the
    lead's salesperson (SPEC.md §14 to §24, §38 to §46, R29 to R31, D2, D6).

    The owner is the conversation's member: Discuss then notifies them, and
    only them, of the customer's messages (§46, R30).
    """

    _inherit = "discuss.channel"

    wa_routed = fields.Boolean(
        "Lead Routing", readonly=True, copy=False,
        help="Created while the account used Lead Routing: one owner, who is the lead's salesperson.",
    )
    wa_lead_stage_id = fields.Many2one(related="wa_lead_id.stage_id", store=True, string="Lead Stage")
    wa_customer_contact = fields.Char(
        "Phone", compute="_compute_wa_customer_contact",
        help="The customer's number, or their WhatsApp username when it is hidden (§45, R16).",
    )
    wa_last_message_preview = fields.Char("Last Message Text", compute="_compute_wa_last_message_preview")
    wa_unread_count = fields.Integer(
        "Unread", compute="_compute_wa_unread_count", search="_search_wa_unread_count",
    )
    wa_user_can_close = fields.Boolean(
        "Can Close", compute="_compute_wa_user_can_close",
        help="The current user owns this routed conversation or is a WhatsApp manager (§44).",
    )

    # ------------------------------------------------------------------
    # Inbox fields (SPEC.md §19, §45)
    # ------------------------------------------------------------------

    @api.depends("wa_customer_phone", "wa_username")
    def _compute_wa_customer_contact(self):
        for channel in self:
            channel.wa_customer_contact = channel.wa_customer_phone or (
                f"@{channel.wa_username}" if channel.wa_username else False
            )

    def _compute_wa_last_message_preview(self):
        texts = {}
        if self.ids:
            # one query for the whole list: each conversation's last message
            self.env["mail.message"].flush_model(["model", "res_id", "message_type", "body"])
            rows = self.env.execute_query(SQL(
                """SELECT DISTINCT ON (res_id) res_id, body FROM mail_message
                    WHERE model = 'discuss.channel' AND res_id IN %s
                      AND message_type IN ('whatsapp_message', 'comment')
                 ORDER BY res_id, id DESC""",
                tuple(self.ids),
            ))
            texts = {res_id: html2plaintext(body or "") for res_id, body in rows}
        for channel in self:
            text = " ".join((texts.get(channel.id) or "").split())
            channel.wa_last_message_preview = (
                text[:PREVIEW_LENGTH - 1] + "…" if len(text) > PREVIEW_LENGTH else text
            ) or False

    @api.depends_context("uid")
    def _compute_wa_unread_count(self):
        for channel in self:
            channel.wa_unread_count = channel.self_member_id.message_unread_counter

    @api.depends_context("uid")
    @api.depends("wa_routed", "wa_assigned_user_id")
    def _compute_wa_user_can_close(self):
        is_manager = self._wa_is_manager()
        for channel in self:
            channel.wa_user_can_close = channel.wa_routed and (
                is_manager or channel.wa_assigned_user_id == self.env.user
            )

    def _search_wa_unread_count(self, operator, value):
        if operator not in (">", "!=") or value not in (0, False):
            raise UserError(self.env._("Only conversations with unread messages can be searched."))
        # the same count as discuss.channel.member._compute_message_unread
        self.env["mail.message"].flush_model(["model", "res_id", "message_type"])
        self.env["discuss.channel.member"].flush_model(["channel_id", "partner_id", "new_message_separator"])
        rows = self.env.execute_query(SQL(
            """SELECT member.channel_id FROM discuss_channel_member member
                WHERE member.partner_id = %s
                  AND EXISTS (SELECT 1 FROM mail_message message
                               WHERE message.model = 'discuss.channel'
                                 AND message.res_id = member.channel_id
                                 AND message.message_type NOT IN ('notification', 'user_notification')
                                 AND message.id >= member.new_message_separator)""",
            self.env.user.partner_id.id,
        ))
        return [("id", "in", [row[0] for row in rows])]

    # ------------------------------------------------------------------
    # Discuss UI data
    # ------------------------------------------------------------------

    def _wa_store_fields(self):
        return super()._wa_store_fields() + [
            Store.Attr("wa_routed", predicate=is_whatsapp_channel),
            Store.Attr("wa_owner_name", lambda c: c.wa_assigned_user_id.name or False, predicate=is_routed),
            Store.Attr(
                "wa_owner_partner_id", lambda c: c.wa_assigned_user_id.partner_id.id or False,
                predicate=is_routed,
            ),
        ]

    def _to_store_defaults(self, target):
        fields_ = super()._to_store_defaults(target)
        if target.is_current_user(self.env):
            is_manager = self.env.user.has_group(MANAGER_GROUP)
            # R30: only managers add people to a routed conversation
            fields_.append(Store.Attr("wa_can_manage", is_manager, predicate=is_routed))
        return fields_

    # ------------------------------------------------------------------
    # Routing (SPEC.md §15, §16, §22, §40, §43, §44)
    # ------------------------------------------------------------------

    @api.model
    def _wa_route_new(self, account, partner, identity):
        """A new customer conversation in Lead Routing: owner, lead, audit (§15, §40)."""
        channel = self._wa_create_conversation(
            account, partner, identity, members=self.env["res.users"], routed=True,
        )
        lead = channel._wa_find_lead()
        Config = self.env["whatsapp_connector.routing.config"]
        if lead and Config._wa_can_own(lead.user_id, account):
            # the customer already has a salesperson (R31): no round-robin
            owner, reason = lead.user_id, "lead"
        else:
            owner, reason = account._wa_routing_config()._wa_next_user(), "round_robin"
        channel._wa_link_lead(lead, owner)
        channel._wa_assign(owner, reason=reason)
        return channel

    def _wa_route_reopened(self):
        """A customer writes again in a closed conversation (§44, Q2).

        The thread reopens; the owner stays while still eligible, otherwise the
        conversation is routed again; a won or lost lead gives way to a new one.
        """
        self.ensure_one()
        channel = self.sudo()
        account = channel.wa_account_id
        config = account._wa_routing_config()
        owner = channel.wa_assigned_user_id
        reason = "kept"
        if owner not in config._wa_eligible_users():
            owner, reason = config._wa_next_user(), "round_robin"
        lead = channel.wa_lead_id
        if not lead or not lead.active or lead.won_status != "pending":
            lead = channel._wa_find_lead().sudo()
            channel._wa_link_lead(lead, owner)
        channel._wa_audit(self.env._("The customer wrote again: conversation reopened."))
        channel._wa_assign(owner, reason=reason)

    def _wa_link_lead(self, lead, owner):
        """Link the customer's lead, creating it when there is none (§15)."""
        self.ensure_one()
        channel = self.sudo()
        if not lead:
            # sudo: the lead belongs to the owner; created by the processing cron
            lead = self.env["crm.lead"].sudo().with_context(wa_owner_sync=True).create(
                channel._wa_lead_values(owner),
            )
        channel.wa_lead_id = lead
        return lead

    def _wa_assign(self, user, reason=False):
        """The one place a routed conversation changes owner (§18, §21, §23, R31).

        The owner is the only member (plus anyone a manager added), and the
        linked lead's salesperson always follows.
        """
        self.ensure_one()
        channel = self.sudo().with_context(wa_assigning=True)
        previous = channel.wa_assigned_user_id
        lead = channel.wa_lead_id
        if lead and lead.user_id != user:
            lead.with_context(wa_owner_sync=True).write({"user_id": user.id or False})
        if previous == user:
            if not user:
                # still nobody: say so and tell the managers (§40)
                channel._wa_audit(channel._wa_assignment_text(user, previous, reason))
                channel._wa_notify_unassigned()
            elif reason == "kept":
                channel._wa_audit(self.env._("Still assigned to %s.", user.name))
            return
        channel.write({
            "wa_assigned_user_id": user.id or False,
            "wa_assigned_team_id": (lead.team_id or user.sale_team_id).id if user else False,
        })
        if user and user.partner_id not in channel.channel_member_ids.partner_id:
            channel._add_members(partners=user.partner_id, post_joined_message=False)
        if previous:
            channel.channel_member_ids.filtered(lambda m: m.partner_id == previous.partner_id).unlink()
        channel._wa_audit(channel._wa_assignment_text(user, previous, reason))
        if not user:
            channel._wa_notify_unassigned()

    def _wa_assignment_text(self, user, previous, reason):
        """§52: to whom, from whom, and why or by whom."""
        tr = self.env._
        names = {"user": user.name, "previous": previous.name, "by": self.env.user.name}
        if not user and previous and reason == "manager":
            return tr("Unassigned from %(previous)s by %(by)s.", **names)
        if not user and previous:
            return tr("Unassigned from %(previous)s: waiting for a salesperson.", **names)
        if not user:
            return tr("Waiting for a salesperson: no eligible user is available.")
        if previous:
            texts = {
                "round_robin": tr("Reassigned from %(previous)s to %(user)s (round-robin).", **names),
                "lead": tr("Reassigned from %(previous)s to %(user)s, the lead's new salesperson.", **names),
                "sender": tr("Reassigned from %(previous)s to %(user)s, who sent a template from the lead.",
                             **names),
            }
            return texts.get(reason) or tr("Reassigned from %(previous)s to %(user)s by %(by)s.", **names)
        texts = {
            "round_robin": tr("Assigned to %s (round-robin).", user.name),
            "lead": tr("Assigned to %s, the lead's salesperson.", user.name),
            "take": tr("%s took the conversation.", user.name),
            "sender": tr("Assigned to %s, who sent a template to the customer.", user.name),
        }
        return texts.get(reason) or tr("Assigned to %(user)s by %(by)s.", **names)

    def _wa_audit(self, text):
        """Audit trail in the conversation itself (§52)."""
        self.sudo().with_context(wa_skip_send=True).message_post(body=text, message_type="notification")

    def _wa_notify_unassigned(self):
        """§40: never silently lose a conversation; tell the managers on the lead."""
        self.ensure_one()
        account = self.wa_account_id
        managers = self.env.ref(MANAGER_GROUP).sudo().all_user_ids.filtered(
            lambda u: u.active and not u.share and account.company_id in u.company_ids,
        )
        lead = self.wa_lead_id.sudo()
        if lead and managers:
            lead.message_post(
                body=self.env._("New WhatsApp conversation waiting for a salesperson: %s",
                                self._get_html_link()),
                partner_ids=managers.partner_id.ids,
                message_type="comment", subtype_xmlid="mail.mt_note",
            )

    # ------------------------------------------------------------------
    # Templates in Lead Routing (D2, D6, Q1)
    # ------------------------------------------------------------------

    def _wa_assign_sender(self, user, record):
        """Ownership after ``user`` sends a template from ``record`` (D2, D6, Q1).

        A new or unassigned conversation becomes the sender's (D2). From a
        lead, the lead and the conversation go to the sender (D6, R31). An
        existing conversation of another salesperson keeps its owner (Q1).
        """
        self.ensure_one()
        channel = self.sudo()
        if record._name == "crm.lead":
            lead = record.sudo()
            previous = lead.user_id
            channel.wa_lead_id = lead
            if previous and previous != user:
                lead.message_post(
                    body=self.env._(
                        "Reassigned to %(user)s, who sent a WhatsApp template from this lead.",
                        user=user.name,
                    ),
                    partner_ids=previous.partner_id.ids,
                    message_type="comment", subtype_xmlid="mail.mt_note",
                )
            channel._wa_assign(user, reason="sender")
        elif not channel.wa_assigned_user_id:
            channel._wa_link_lead(channel.wa_lead_id or channel._wa_find_lead().sudo(), user)
            channel._wa_assign(user, reason="sender")

    # ------------------------------------------------------------------
    # Guards (SPEC.md §21, §61, R30)
    # ------------------------------------------------------------------

    def _wa_is_manager(self):
        return self.env.su or self.env.user.has_group(MANAGER_GROUP)

    def add_members(self, *args, **kwargs):
        if not any(is_routed(channel) for channel in self):
            return super().add_members(*args, **kwargs)
        if not self._wa_is_manager():
            raise AccessError(self.env._(
                "Only WhatsApp managers can add people to a conversation assigned by Lead Routing.",
            ))
        # sudo: managers add people to routed conversations they are not members of
        # (Discuss only lets members invite); checked just above (R30)
        return super(DiscussChannel, self.sudo()).add_members(*args, **kwargs)

    def write(self, vals):
        if "wa_assigned_user_id" not in vals or self.env.context.get("wa_assigning"):
            return super().write(vals)
        # reassignment by hand goes through _wa_assign (§18)
        vals = dict(vals)
        user = self.env["res.users"].browse(vals.pop("wa_assigned_user_id"))
        if not self._wa_is_manager():
            raise AccessError(self.env._("Only WhatsApp managers can reassign conversations."))
        result = super().write(vals) if vals else True
        for channel in self.filtered(is_routed):
            channel._wa_assign(user, reason="manager")
        return result

    def _wa_check_can_send(self, attachments, text=""):
        self.ensure_one()
        if self.wa_routed and self.env.user.partner_id not in self.sudo().channel_member_ids.partner_id:
            owner = self.sudo().wa_assigned_user_id
            raise UserError(
                self.env._("This conversation is assigned to %s.", owner.name) if owner
                else self.env._("Take this conversation before writing in it."),
            )
        return super()._wa_check_can_send(attachments, text)

    # ------------------------------------------------------------------
    # Actions (SPEC.md §18, §44, Q3)
    # ------------------------------------------------------------------

    def action_wa_take(self):
        """Take an unassigned conversation (eligible salespeople), or assign it to me (managers)."""
        self.ensure_one()
        channel = self.sudo()
        user = self.env.user
        if not channel.wa_routed:
            raise UserError(self.env._("Only conversations under Lead Routing have an owner."))
        if not self._wa_is_manager():
            if user not in channel.wa_account_id._wa_routing_config()._wa_eligible_users():
                raise AccessError(self.env._("You do not take part in this account's routing."))
            if channel.wa_assigned_user_id:
                raise UserError(self.env._("%s already took this conversation.", channel.wa_assigned_user_id.name))
        try:
            channel.lock_for_update()
        except LockError:
            raise UserError(self.env._("Someone else is taking this conversation right now.")) from None
        channel._wa_assign(user, reason="take")
        return channel._get_access_action()

    def action_wa_open_chat(self):
        self.ensure_one()
        return self._get_access_action()

    def _wa_check_owner_or_manager(self):
        for channel in self:
            if channel.wa_routed and channel.sudo().wa_assigned_user_id != self.env.user and not self._wa_is_manager():
                raise AccessError(self.env._("Only the owner or a WhatsApp manager can do this."))

    def action_wa_close(self):
        """§44: closing keeps the history; the customer's next message reopens it."""
        self._wa_check_owner_or_manager()
        for channel in self.sudo().filtered(lambda c: c.wa_status == "open"):
            channel.wa_status = "closed"
            channel._wa_audit(self.env._("Conversation closed by %s.", self.env.user.name))
        return True

    def action_wa_reopen(self):
        self._wa_check_owner_or_manager()
        for channel in self.sudo().filtered(lambda c: c.wa_status == "closed"):
            # sudo: the other conversation may belong to another salesperson
            if channel.search_count([
                ("channel_type", "=", "whatsapp"), ("wa_account_id", "=", channel.wa_account_id.id),
                ("wa_customer_key", "=", channel.wa_customer_key), ("wa_status", "=", "open"),
            ]):
                raise UserError(self.env._("This customer already has an open conversation."))
            channel._wa_reopen()
            channel._wa_audit(self.env._("Conversation reopened by %s.", self.env.user.name))
        return True
