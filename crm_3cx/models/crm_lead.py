# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models

from .crm_3cx_call import ANSWERED, ATTEMPT_PREFIX, ATTEMPTS, CALLBACK_PREFIX, FIRST_CALL_PREFIX
from .res_config_settings import (
    PARAM_ADVANCE_STAGE, PARAM_CADENCE_HOURS, PARAM_FIRST_CALL_MINUTES, PARAM_MAX_ATTEMPTS,
    PARAM_MISSED_ACTIVITY, PARAM_RENEWAL_DAYS, PARAM_SLA_ESCALATE, dial_url, param_flag, param_hours, param_int,
)

SPEED_BUCKETS = [
    ("5m", "Within 5 minutes"),
    ("1h", "Within 1 hour"),
    ("24h", "Within 24 hours"),
    ("later", "Later than 24 hours"),
    ("never", "Never called"),
]


class CrmLead(models.Model):
    _inherit = "crm.lead"

    threecx_call_ids = fields.One2many("crm.3cx.call", "lead_id", string="3CX Calls")
    threecx_call_count = fields.Integer(compute="_compute_threecx_stats", store=True)
    threecx_attempts = fields.Integer("Dial Attempts", compute="_compute_threecx_stats", store=True,
                                      help="Outbound calls made, answered or not.")
    threecx_conversations = fields.Integer("Conversations", compute="_compute_threecx_stats", store=True,
                                           help="Calls, in or out, that were actually answered.")
    threecx_missed = fields.Integer("Missed Calls", compute="_compute_threecx_stats", store=True)
    threecx_talk_time = fields.Integer("Talk Time (s)", compute="_compute_threecx_stats", store=True)
    threecx_last_call = fields.Datetime("Last Call", compute="_compute_threecx_stats", store=True)
    threecx_first_touch = fields.Datetime("First Call Attempt", compute="_compute_threecx_stats", store=True,
                                          help="First outbound dial or answered inbound call.")
    threecx_first_conversation = fields.Datetime("First Conversation", compute="_compute_threecx_stats", store=True)
    threecx_speed_to_lead = fields.Float("Speed to Lead (min)", compute="_compute_threecx_stats", store=True,
                                         help="Minutes from the lead's creation to the first call attempt.")
    threecx_speed_bucket = fields.Selection(SPEED_BUCKETS, "Speed to Lead", compute="_compute_threecx_stats", store=True)
    threecx_sla_deadline = fields.Datetime("First Call Due", readonly=True)
    threecx_sla_state = fields.Selection(
        [("pending", "Waiting for first call"), ("met", "Called in time"), ("breached", "First call overdue")],
        "First Call SLA", readonly=True, index=True,
    )
    threecx_kind = fields.Selection(
        [("renewal", "Renewal"), ("inbound", "Inbound call"), ("chat", "Chat")], "Created from", readonly=True,
    )
    threecx_renewal_date = fields.Date("Renewal Date", readonly=True)

    # ── metrics ─────────────────────────────────────────────────────────────

    @api.depends("threecx_call_ids.call_type", "threecx_call_ids.started_at", "threecx_call_ids.duration", "create_date")
    def _compute_threecx_stats(self):
        for lead in self:
            calls = lead.threecx_call_ids
            attempts = calls.filtered(lambda c: c.call_type in ATTEMPTS)
            answered = calls.filtered(lambda c: c.call_type in ANSWERED)
            touches = calls.filtered(lambda c: c.call_type in ATTEMPTS or c.call_type == "inbound")
            started = [c.started_at for c in calls if c.started_at]
            first_touch = min((c.started_at for c in touches if c.started_at), default=False)
            lead.threecx_call_count = len(calls)
            lead.threecx_attempts = len(attempts)
            lead.threecx_conversations = len(answered)
            lead.threecx_missed = len(calls.filtered(lambda c: c.call_type == "missed"))
            lead.threecx_talk_time = sum(answered.mapped("duration"))
            lead.threecx_last_call = max(started, default=False)
            lead.threecx_first_touch = first_touch
            lead.threecx_first_conversation = min((c.started_at for c in answered if c.started_at), default=False)
            if first_touch and lead.create_date:
                minutes = max(0.0, (first_touch - lead.create_date).total_seconds() / 60)
                lead.threecx_speed_to_lead = minutes
                lead.threecx_speed_bucket = ("5m" if minutes <= 5 else "1h" if minutes <= 60
                                             else "24h" if minutes <= 1440 else "later")
            else:
                lead.threecx_speed_to_lead = 0
                lead.threecx_speed_bucket = "never"

    def action_view_threecx_calls(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("crm_3cx.action_crm_3cx_call")
        action["domain"] = [("lead_id", "=", self.id)]
        action["context"] = {"default_lead_id": self.id}
        return action

    def action_threecx_call(self):
        """Dial this lead through 3CX. The call itself comes back through
        ReportCall, which closes or reschedules the activity."""
        self.ensure_one()
        url = dial_url(self.env, self.phone_sanitized or self.phone or self.partner_id.phone)
        if not url:
            return False
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    # ── speed-to-lead SLA ───────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        leads._threecx_start_sla()
        return leads

    def _threecx_assignee(self, agent=None):
        """Who should make the call: the salesperson, else the agent 3CX named,
        else the team leader, else the administrator."""
        self.ensure_one()
        return self.user_id or (agent if agent else self.team_id.user_id) or self.env.ref("base.user_admin")

    def _threecx_start_sla(self):
        """A new lead with a phone number gets a First call activity and a
        deadline; _cron_threecx_sla turns it into a breach if nobody dials."""
        minutes = param_int(self.env, PARAM_FIRST_CALL_MINUTES)
        if not minutes:
            return
        Call = self.env["crm.3cx.call"]
        for lead in self:
            if lead.threecx_call_ids or not (lead.phone or lead.partner_id.phone):
                continue
            lead.write({
                "threecx_sla_deadline": fields.Datetime.now() + timedelta(minutes=minutes),
                "threecx_sla_state": "pending",
            })
            if not Call._open_call_activities(lead):
                Call._schedule_call(
                    lead, FIRST_CALL_PREFIX, user=lead._threecx_assignee(),
                    note=Markup("<p>New lead: call within %d minutes.</p>") % minutes,
                )

    def _threecx_settle_sla(self, call):
        if self.threecx_sla_state != "pending" or call.call_type == "missed":
            return
        when = call.started_at or fields.Datetime.now()
        self.threecx_sla_state = "met" if not self.threecx_sla_deadline or when <= self.threecx_sla_deadline else "breached"

    @api.model
    def _cron_threecx_sla(self):
        """Every few minutes: flag leads nobody dialled in time, and tell the
        team leader."""
        now = fields.Datetime.now()
        overdue = self.search([("threecx_sla_state", "=", "pending"), ("threecx_sla_deadline", "<", now)])
        if not overdue:
            return
        overdue.write({"threecx_sla_state": "breached"})
        if not param_flag(self.env, PARAM_SLA_ESCALATE):
            return
        minutes = param_int(self.env, PARAM_FIRST_CALL_MINUTES)
        for lead in overdue:
            leader = lead.team_id.user_id
            body = Markup("<p><b>First call overdue</b>: no call attempt within %d minutes of this lead's creation.</p>") % minutes
            recipients = leader.partner_id.ids if leader and leader != lead.user_id else []
            lead.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_comment", partner_ids=recipients)
            if leader:
                lead.activity_schedule(
                    "mail.mail_activity_data_todo", summary="First call overdue: %s" % lead.name,
                    note=Markup("<p>Assigned to %s, still not called.</p>") % (lead.user_id.name or "nobody"),
                    user_id=leader.id,
                )

    # ── what a reported call changes on a lead ──────────────────────────────

    def _threecx_after_call(self, call, agent=None):
        """Activities, cadence, stage and SLA. Returns the activity scheduled, if any."""
        self.ensure_one()
        Call = self.env["crm.3cx.call"]
        activity = None
        if call.call_type in ANSWERED:
            Call._close_call_activities(self, "Reached on a 3CX call")
            if param_flag(self.env, PARAM_ADVANCE_STAGE) and self.threecx_conversations == 1:
                self._threecx_advance_stage()
        elif call.call_type == "missed":
            if param_flag(self.env, PARAM_MISSED_ACTIVITY) and not Call._open_call_activities(self):
                activity = Call._schedule_call(
                    self, CALLBACK_PREFIX + (call.number or self.display_name), user=self._threecx_assignee(agent),
                    note=Markup("<p>Missed call%s.</p>") % (" for " + agent.name if agent else ""),
                )
        elif call.call_type == "notanswered":
            Call._close_call_activities(self, "Dialled, no answer")
            if not self.threecx_conversations:
                activity = self._threecx_next_attempt(call, agent)
        self._threecx_settle_sla(call)
        return activity

    def _threecx_next_attempt(self, call, agent=None):
        """After an unanswered dial: schedule the next one per the cadence, or
        give up as Unreachable once the limit is reached."""
        max_attempts = param_int(self.env, PARAM_MAX_ATTEMPTS)
        if max_attempts and self.threecx_attempts >= max_attempts:
            self.action_set_lost(lost_reason_id=self.env.ref("crm_3cx.lost_reason_unreachable").id)
            self.message_post(
                body=Markup("<p>Marked <b>Unreachable</b>: %d dial attempts, no conversation.</p>") % self.threecx_attempts,
                message_type="comment", subtype_xmlid="mail.mt_note",
            )
            return None
        hours = param_hours(self.env, PARAM_CADENCE_HOURS)
        if not hours:
            return None
        delay = hours[min(self.threecx_attempts, len(hours)) - 1]
        due = fields.Date.context_today(self, (call.started_at or fields.Datetime.now()) + timedelta(hours=delay))
        return self.env["crm.3cx.call"]._schedule_call(
            self, "%s%d" % (ATTEMPT_PREFIX, self.threecx_attempts + 1), user=self._threecx_assignee(agent),
            note=Markup("<p>Attempt %d of %s went unanswered; try again.</p>") % (
                self.threecx_attempts, max_attempts or "∞"),
            date_deadline=due,
        )

    def _threecx_advance_stage(self):
        """Leave the pipeline's first stage once someone has actually talked to them."""
        for lead in self:
            if not lead.stage_id or lead.stage_id.is_won:
                continue
            first = lead._stage_find(team_id=lead.team_id.id, domain=[("is_won", "=", False)])
            if lead.stage_id != first:
                continue
            following = lead._stage_find(
                team_id=lead.team_id.id, domain=[("sequence", ">", lead.stage_id.sequence), ("is_won", "=", False)],
            )
            if following:
                lead.stage_id = following

    # ── renewals ────────────────────────────────────────────────────────────

    @api.model
    def _cron_threecx_renewals(self):
        """Daily: a renewal lead for every contact whose renewal date is within
        the configured horizon, once per renewal date."""
        days = param_int(self.env, PARAM_RENEWAL_DAYS)
        if not days:
            return
        today = fields.Date.context_today(self)
        partners = self.env["res.partner"].search([
            ("threecx_renewal_date", ">=", today), ("threecx_renewal_date", "<=", today + timedelta(days=days)),
        ])
        Lead = self.with_context(active_test=False)
        Team = self.env["crm.team"]
        for partner in partners:
            if Lead.search_count([
                ("partner_id", "=", partner.id), ("threecx_kind", "=", "renewal"),
                ("threecx_renewal_date", "=", partner.threecx_renewal_date),
            ]):
                continue
            team = Team._get_default_team_id(user_id=partner.user_id.id or False)
            self.create({
                "name": "Renewal · %s%s" % (partner.name, " · " + partner.threecx_policy_ref if partner.threecx_policy_ref else ""),
                "type": "opportunity" if not self._threecx_leads_enabled() else "lead",
                "partner_id": partner.id,
                "phone": partner.phone or False,
                "email_from": partner.email or False,
                "user_id": partner.user_id.id or team.user_id.id or False,
                "team_id": team.id or False,
                "date_deadline": partner.threecx_renewal_date,
                "threecx_kind": "renewal",
                "threecx_renewal_date": partner.threecx_renewal_date,
            })

    @api.model
    def _threecx_leads_enabled(self):
        group = self.env.ref("crm.group_use_lead")
        return group in self.env.ref("base.group_user").sudo().all_implied_ids
