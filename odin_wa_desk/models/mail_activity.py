from odoo import api, models
from odoo.tools import format_date


class MailActivity(models.Model):
    _inherit = "mail.activity"

    @api.onchange("activity_type_id")
    def _onchange_activity_type_id(self):
        """Keep the client's own sentence as the summary.

        The Call type ships the summary "Call", and the stock onchange copies a
        type's summary over whatever is there. Every follow-up would then be
        called "Call" in the assignee's Activities list, which is where they
        decide what to do next. Only conversations going through
        ``action_wa_schedule_call`` are affected; the type's date and assignee
        defaults are still applied as usual.
        """
        kept = self.summary
        super()._onchange_activity_type_id()
        if kept and self.env.context.get("wa_note_channel_id"):
            self.summary = kept

    @api.model_create_multi
    def create(self, vals_list):
        """Note a follow-up scheduled from a WhatsApp conversation back in it.

        Without this the conversation says nothing and the next person to read
        it schedules a second call or answers themselves. The context key is set
        only by ``action_wa_schedule_call``, so no other activity is touched.
        """
        activities = super().create(vals_list)
        channel_id = self.env.context.get("wa_note_channel_id")
        if not channel_id:
            return activities
        channel = self.env["discuss.channel"].sudo().browse(channel_id).exists()
        if not channel:
            return activities
        for activity in activities.sudo():
            channel.with_context(wa_skip_send=True).message_post(
                body=self.env._(
                    "%(what)s scheduled for %(who)s on %(when)s.",
                    what=activity.activity_type_id.name or self.env._("Follow-up"),
                    who=activity.user_id.name or self.env._("nobody"),
                    when=format_date(self.env, activity.date_deadline),
                ),
                message_type="notification",
            )
        return activities
