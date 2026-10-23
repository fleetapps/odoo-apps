from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_round


def _tz_get(self):
    return [(tz, tz) for tz in sorted(pytz.all_timezones, key=lambda tz: tz if not tz.startswith("Etc/") else "_")]


class WhatsappAccount(models.Model):
    """When and how the account answers a customer nobody has answered yet."""

    _inherit = "whatsapp_connector.account"

    odin_autoreply = fields.Boolean(
        "Auto-reply", tracking=True,
        help="Answer customers nobody has answered: at once outside business hours, after the "
        "delay below during them. The reply sends the online quote page that fits their message, "
        "or tells them an advisor will reply; the team is told while they wait.")
    odin_autoreply_delay = fields.Integer(
        "Wait during business hours (minutes)", default=10,
        help="How long agents have to answer before the auto-reply goes out.")
    odin_tz = fields.Selection(_tz_get, "Time zone", default="Africa/Nairobi", required=True)
    odin_open_hour = fields.Float("Weekdays from", default=8.0)
    odin_close_hour = fields.Float("Weekdays until", default=17.0)
    odin_saturday_open_hour = fields.Float("Saturday from", default=9.0)
    odin_saturday_close_hour = fields.Float(
        "Saturday until", default=13.0, help="The same as 'from' for closed on Saturdays.")
    odin_alert_after = fields.Float(
        "Tell the team after (working hours)", default=2.0,
        help="A customer still waiting for a person after this many working hours: the "
        "conversation's owner, else the Notify users, else the online quotes team, else the "
        "sales managers, are told.")
    odin_window_alert = fields.Float(
        "Escalate before the window closes (hours)", default=4.0,
        help="WhatsApp allows free messages for 24 hours after the customer's last one. This "
        "long before that, with no reply yet, the people below get a to-do.")
    odin_escalate_user_ids = fields.Many2many(
        "res.users", "odin_wa_account_escalate_rel", "account_id", "user_id",
        string="Escalate to", domain=[("share", "=", False)],
        help="Who gets the to-do before the window closes, and service messages (payments, "
        "claims) at once. Empty: the WhatsApp managers, else the sales managers.")

    @api.constrains("odin_open_hour", "odin_close_hour", "odin_saturday_open_hour",
                    "odin_saturday_close_hour", "odin_autoreply_delay")
    def _check_odin_hours(self):
        for account in self:
            hours = (account.odin_open_hour, account.odin_close_hour,
                     account.odin_saturday_open_hour, account.odin_saturday_close_hour)
            if any(not 0 <= hour <= 24 for hour in hours):
                raise ValidationError(_("Business hours are between 0:00 and 24:00."))
            if account.odin_close_hour <= account.odin_open_hour:
                raise ValidationError(_("Weekdays must end after they start."))
            if account.odin_saturday_close_hour < account.odin_saturday_open_hour:
                raise ValidationError(_("Saturdays must end after they start."))
            if account.odin_autoreply_delay < 0:
                raise ValidationError(_("The wait cannot be negative."))

    # -- business hours -----------------------------------------------------

    def _odin_hours(self, day):
        """``(open, close)`` local datetimes of ``day`` (a date), or None if closed."""
        self.ensure_one()
        weekday = day.weekday()
        if weekday < 5:
            start, end = self.odin_open_hour, self.odin_close_hour
        elif weekday == 5:
            start, end = self.odin_saturday_open_hour, self.odin_saturday_close_hour
        else:
            return None
        if end <= start:
            return None

        def at(hour):
            minutes = int(float_round(hour * 60, precision_digits=0))
            return datetime.combine(day, time()) + timedelta(minutes=minutes)

        return at(start), at(end)

    def _odin_local(self, moment):
        """A naive UTC datetime, as naive local time."""
        tz = pytz.timezone(self.odin_tz or "Africa/Nairobi")
        return pytz.utc.localize(moment).astimezone(tz).replace(tzinfo=None)

    def _odin_utc(self, local):
        tz = pytz.timezone(self.odin_tz or "Africa/Nairobi")
        return tz.localize(local).astimezone(pytz.utc).replace(tzinfo=None)

    def _odin_is_open(self, moment):
        self.ensure_one()
        local = self._odin_local(moment)
        hours = self._odin_hours(local.date())
        return bool(hours and hours[0] <= local < hours[1])

    def _odin_next_opening(self, moment):
        """The next time the business opens after ``moment`` (both naive UTC)."""
        self.ensure_one()
        local = self._odin_local(moment)
        for offset in range(8):
            hours = self._odin_hours(local.date() + timedelta(days=offset))
            if hours and hours[0] > local:
                return self._odin_utc(hours[0])
        return moment

    def _odin_working_seconds(self, start, end):
        """Business-hours seconds between two naive UTC datetimes."""
        self.ensure_one()
        if end <= start:
            return 0.0
        local_start, local_end = self._odin_local(start), self._odin_local(end)
        total, day = 0.0, local_start.date()
        while day <= local_end.date():
            hours = self._odin_hours(day)
            if hours:
                low, high = max(hours[0], local_start), min(hours[1], local_end)
                if high > low:
                    total += (high - low).total_seconds()
            day += timedelta(days=1)
        return total

    def _odin_when_text(self, moment):
        """'shortly' while open, else 'from 8am tomorrow' / 'from 9am on Saturday'."""
        self.ensure_one()
        if self._odin_is_open(moment):
            return _("shortly")
        opening = self._odin_local(self._odin_next_opening(moment))
        today = self._odin_local(moment).date()
        hour = opening.strftime("%-I:%M%p").lower().replace(":00", "")
        if opening.date() == today:
            return _("from %s", hour)
        if opening.date() == today + timedelta(days=1):
            return _("from %s tomorrow", hour)
        return _("from %(hour)s on %(day)s", hour=hour, day=opening.strftime("%A"))

    def _odin_escalation_users(self):
        self.ensure_one()
        users = self.odin_escalate_user_ids
        if not users:
            users = self.env.ref("whatsapp_connector.group_whatsapp_manager").sudo().all_user_ids
        if not users:
            users = self.env.ref("sales_team.group_sale_manager").sudo().all_user_ids
        return users.filtered(
            lambda u: u.active and not u.share and self.company_id in u.company_ids)
