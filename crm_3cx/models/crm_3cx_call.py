# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
from odoo import api, fields, models

CALL_TYPES = [
    ("inbound", "Inbound"),
    ("missed", "Missed"),
    ("outbound", "Outbound"),
    ("notanswered", "Outbound, not answered"),
]
ANSWERED = ("inbound", "outbound")
ATTEMPTS = ("outbound", "notanswered")  # dials made by the team

# Summaries of the call activities this module schedules, so it can find and
# close its own without touching activities people created by hand.
CALLBACK_PREFIX = "Call back "
FIRST_CALL_PREFIX = "First call"
ATTEMPT_PREFIX = "Call attempt "
OWN_ACTIVITY_DOMAIN = ["|", "|",
                       ("summary", "=like", CALLBACK_PREFIX + "%"),
                       ("summary", "=like", FIRST_CALL_PREFIX + "%"),
                       ("summary", "=like", ATTEMPT_PREFIX + "%")]


class Crm3cxCall(models.Model):
    """One row per call 3CX reported, so calls can be listed, grouped and
    charted like any other Odoo record. The chatter note is the narrative;
    this is the data."""
    _name = "crm.3cx.call"
    _description = "3CX Call"
    _order = "started_at desc, id desc"
    _rec_name = "name"

    name = fields.Char(compute="_compute_name", store=True)
    call_type = fields.Selection(CALL_TYPES, required=True, index=True)
    direction = fields.Selection(
        [("inbound", "Inbound"), ("outbound", "Outbound")], compute="_compute_flags", store=True, index=True,
    )
    answered = fields.Boolean(compute="_compute_flags", store=True)
    # 1/0 so pivots can sum them into rates: connect rate = conversations / attempts.
    is_attempt = fields.Integer("Attempt", compute="_compute_flags", store=True)
    is_conversation = fields.Integer("Conversation", compute="_compute_flags", store=True)
    number = fields.Char("Number", index=True)
    partner_id = fields.Many2one("res.partner", "Contact", index=True, ondelete="set null")
    lead_id = fields.Many2one("crm.lead", "Lead / Opportunity", index=True, ondelete="set null")
    user_id = fields.Many2one("res.users", "Agent", index=True, ondelete="set null")
    extension = fields.Char("Extension")
    queue = fields.Char("Queue")
    started_at = fields.Datetime("Started", index=True)
    started_hour = fields.Integer("Hour of Day", compute="_compute_started_hour", store=True,
                                  help="In the company's time zone. Group by it to find the best hours to call.")
    duration = fields.Integer("Duration (s)")
    duration_display = fields.Char("Duration", compute="_compute_duration_display")
    recording_url = fields.Char("Recording")
    summary = fields.Text("AI Summary")
    sentiment = fields.Char("Sentiment")
    transcription = fields.Text("Transcription")
    message_id = fields.Many2one("mail.message", "Chatter Note", ondelete="set null")
    activity_id = fields.Many2one("mail.activity", "Scheduled Activity", ondelete="set null")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True)

    @api.depends("call_type", "number", "partner_id.display_name", "lead_id.display_name")
    def _compute_name(self):
        labels = dict(CALL_TYPES)
        for call in self:
            who = call.partner_id.display_name or call.lead_id.display_name or call.number or "unknown number"
            call.name = "%s · %s" % (labels.get(call.call_type, "Call"), who)

    @api.depends("call_type")
    def _compute_flags(self):
        for call in self:
            call.direction = "inbound" if call.call_type in ("inbound", "missed") else "outbound"
            call.answered = call.call_type in ANSWERED
            call.is_attempt = int(call.call_type in ATTEMPTS)
            call.is_conversation = int(call.call_type in ANSWERED)

    @api.depends("started_at", "company_id.partner_id.tz")
    def _compute_started_hour(self):
        for call in self:
            if not call.started_at:
                call.started_hour = -1
                continue
            tz = call.company_id.partner_id.tz or self.env.user.tz or "UTC"
            call.started_hour = fields.Datetime.context_timestamp(call.with_context(tz=tz), call.started_at).hour

    @api.depends("duration")
    def _compute_duration_display(self):
        for call in self:
            seconds = call.duration or 0
            call.duration_display = "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)

    @api.model
    def _parse_duration(self, text):
        """3CX sends "hh:mm:ss"; anything else yields 0 rather than an error."""
        parts = (text or "").strip().split(":")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return 0
        hours, minutes, seconds = (int(p) for p in parts)
        return hours * 3600 + minutes * 60 + seconds

    @api.model
    def _parse_started_at(self, text):
        """The template sends CallStartTimeUTC as yyyy-MM-ddTHH:mm:ssZ."""
        text = (text or "").strip()
        try:
            return fields.Datetime.to_datetime(text.replace("T", " ").rstrip("Z")) or False
        except ValueError:
            return False

    # ── the call activities this module manages, on contacts and leads ──────

    @api.model
    def _open_call_activities(self, record):
        return record.activity_search(["mail.mail_activity_data_call"], additional_domain=OWN_ACTIVITY_DOMAIN)

    @api.model
    def _close_call_activities(self, record, feedback):
        self._open_call_activities(record).action_feedback(feedback=feedback)

    @api.model
    def _schedule_call(self, record, summary, user, note="", date_deadline=None):
        return record.activity_schedule(
            "mail.mail_activity_data_call", summary=summary, note=note, user_id=user.id,
            date_deadline=date_deadline or fields.Date.context_today(record),
        )
