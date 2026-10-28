"""Renewals: every policy coming up for renewal, and the reminders that make
sure someone acts on it.

A renewal is its own record, not an opportunity: a policy sold today renews
in a year, and an opportunity open for a year is noise in the pipeline. The
opportunity is opened when it is time to act -- 30 days before -- and the
person responsible is emailed then, a week before, on the day, and once if it
is missed. A renewal not renewed 30 days after its date has lapsed.

Renewals come from three places: a quote the client accepts (the next
renewal of what was sold), a lead whose current policy elsewhere renews on a
date (a prospect), or by hand.
"""

import logging
from email.utils import formataddr
from urllib.parse import urlsplit

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize, format_date, formatLang

_logger = logging.getLogger(__name__)

#: days before the renewal date a reminder goes out, the first opening the
#: opportunity
REMINDER_DAYS = (30, 7, 0)
#: days after the renewal date it is given up as lapsed
LAPSE_AFTER = 30
#: renewals in the weekly summary: due within this many days
DIGEST_AHEAD = 14
OPEN_STATES = ("upcoming", "in_progress")


class Renewal(models.Model):
    _name = "odin.renewal"
    _description = "Policy renewal"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "renewal_date, id"
    _rec_names_search = ["name", "partner_id", "policy_number"]

    name = fields.Char(compute="_compute_name", store=True)
    partner_id = fields.Many2one("res.partner", string="Client", index=True, tracking=True)
    client_name = fields.Char(
        compute="_compute_client_name", string="Client name",
        help="The contact, or the lead's contact for a prospect not yet in Contacts.")
    line = fields.Selection(selection="_selection_line", string="Class", required=True,
                            default="medical", tracking=True)
    insurer_id = fields.Many2one(
        "res.partner", string="Current insurer", domain=[("is_company", "=", True)], tracking=True)
    product_id = fields.Many2one("odin.cover.product", string="Product")
    policy_number = fields.Char(tracking=True)
    cover_start = fields.Date(tracking=True)
    period_months = fields.Integer(string="Policy period (months)", default=12)
    start_to_confirm = fields.Boolean(
        string="Start date to confirm",
        help="Confirmed without a cover start date: the renewal date counts from the "
        "confirmation. Put it right with Cover placed.")
    renewal_date = fields.Date(required=True, tracking=True, index=True)
    premium = fields.Monetary(string="Current premium", currency_field="currency_id")
    user_id = fields.Many2one(
        "res.users", string="Responsible", tracking=True, domain=[("share", "=", False)],
        default=lambda self: self.env.user)
    origin = fields.Selection(
        [("ours", "Sold by us"), ("prospect", "Insured elsewhere")],
        string="Policy", default="prospect", required=True)
    state = fields.Selection(
        [("upcoming", "Upcoming"), ("in_progress", "In progress"), ("renewed", "Renewed"),
         ("lost", "Lost"), ("lapsed", "Lapsed"), ("cancelled", "Cancelled")],
        default="upcoming", required=True, tracking=True, index=True)
    lead_id = fields.Many2one(
        "crm.lead", string="Opportunity", index=True, tracking=True, ondelete="set null",
        help="Where the renewal is worked: the prospect's lead, or the renewal opportunity "
        "opened 30 days before.")
    source_lead_id = fields.Many2one(
        "crm.lead", string="Won opportunity", ondelete="set null", readonly=True,
        help="The opportunity the policy was sold on.")
    comparison_id = fields.Many2one(
        "odin.quote.comparison", string="Quote sold", ondelete="set null", readonly=True)
    order_id = fields.Many2one("sale.order", string="Order", ondelete="set null", readonly=True)
    quote_ids = fields.One2many("odin.quote.comparison", "odin_renewal_id", string="Renewal quotes")
    quote_count = fields.Integer(compute="_compute_quote_count")
    next_renewal_id = fields.Many2one("odin.renewal", string="Renewed as", readonly=True)
    reminders_sent = fields.Char(copy=False, readonly=True)
    days_left = fields.Integer(compute="_compute_days_left")
    line_label = fields.Char(compute="_compute_labels")
    state_label = fields.Char(compute="_compute_labels")
    note = fields.Text()
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")

    @api.model
    def _selection_line(self):
        return self.env["odin.quote.comparison"]._fields["odin_quote_type"].selection

    @api.depends("partner_id", "lead_id.contact_name", "lead_id.partner_name", "lead_id.name")
    def _compute_client_name(self):
        for renewal in self:
            lead = renewal.lead_id
            renewal.client_name = (renewal.partner_id.name or lead.contact_name
                                   or lead.partner_name or lead.name or "")

    @api.depends("line", "partner_id", "policy_number", "insurer_id", "lead_id")
    def _compute_name(self):
        lines = dict(self._selection_line())
        for renewal in self:
            parts = [lines.get(renewal.line, ""), renewal.client_name or _("New client")]
            if renewal.policy_number:
                parts.append(renewal.policy_number)
            elif renewal.insurer_id:
                parts.append(renewal.insurer_id.name)
            renewal.name = " – ".join(p for p in parts if p)

    @api.depends("line", "state")
    def _compute_labels(self):
        lines = dict(self._selection_line())
        states = dict(self._fields["state"]._description_selection(self.env))
        for renewal in self:
            renewal.line_label = lines.get(renewal.line, "")
            renewal.state_label = states.get(renewal.state, "")

    @api.depends("quote_ids")
    def _compute_quote_count(self):
        for renewal in self:
            renewal.quote_count = len(renewal.quote_ids)

    @api.depends("renewal_date")
    @api.depends_context("odin_today")
    def _compute_days_left(self):
        today = self.env.context.get("odin_today") or fields.Date.context_today(self)
        for renewal in self:
            renewal.days_left = (renewal.renewal_date - today).days if renewal.renewal_date else 0

    @api.onchange("cover_start", "period_months")
    def _onchange_cover_start(self):
        if self.cover_start and self.period_months:
            self.renewal_date = self.cover_start + relativedelta(months=self.period_months)

    # -- who acts on it -----------------------------------------------------

    def _odin_responsible(self):
        """The person named on it, else the opportunity's salesperson, else
        whoever places cover, else the company's administrator."""
        self.ensure_one()
        for user in (self.user_id, self.lead_id.user_id, self.source_lead_id.user_id,
                     self.company_id.odin_quote_handler_id):
            if user and user.active and not user.share:
                return user
        return self.env.ref("base.user_admin")

    def _odin_manager(self):
        self.ensure_one()
        user = self.company_id.odin_renewal_digest_user_id
        return user if user and user.active else self.env["res.users"]

    # -- the opportunity ----------------------------------------------------

    def _odin_ensure_opportunity(self):
        """The opportunity the renewal is worked on: the prospect's lead
        while it is open, otherwise a new one dated for the renewal."""
        self.ensure_one()
        lead = self.lead_id
        if lead and lead.active and lead.won_status != "won":
            if not lead.date_deadline or lead.date_deadline > self.renewal_date:
                lead.date_deadline = self.renewal_date
            return lead
        Lead = self.env["crm.lead"].sudo()
        user = self._odin_responsible()
        source = self.source_lead_id
        lines = dict(self._selection_line())
        tags = self._odin_tag(_("Renewal")) | self._odin_tag(lines.get(self.line, ""))
        lead = Lead.create({
            "name": _("Renewal – %(line)s – %(client)s", line=lines.get(self.line, ""),
                      client=self.client_name),
            "type": "opportunity",
            "partner_id": self.partner_id.id,
            "user_id": user.id,
            "team_id": source.team_id.id or False,
            "expected_revenue": self.order_id.amount_untaxed or self.premium,
            "date_deadline": self.renewal_date,
            "tag_ids": [(6, 0, tags.ids)],
            "description": self._odin_description(),
            "company_id": self.company_id.id,
        })
        self.lead_id = lead
        lead.message_post(body=Markup("%s %s") % (
            _("Opened for the renewal of"), self._get_html_link()))
        return lead

    @api.model
    def _odin_tag(self, name):
        if not name:
            return self.env["crm.tag"]
        Tag = self.env["crm.tag"].sudo()
        return Tag.search([("name", "=", name)], limit=1) or Tag.create({"name": name})

    def _odin_description(self):
        self.ensure_one()
        rows = [
            (_("Renewal date"), format_date(self.env, self.renewal_date, date_format="d MMM y")),
            (_("Current insurer"), self.insurer_id.name),
            (_("Policy number"), self.policy_number),
            (_("Current premium"), formatLang(self.env, self.premium, currency_obj=self.currency_id)
             if self.premium else ""),
        ]
        return Markup("").join(Markup("<p><b>%s:</b> %s</p>") % (label, value)
                               for label, value in rows if value)

    def _odin_schedule_todo(self, lead):
        self.ensure_one()
        user = self._odin_responsible()
        summary = _("Renewal due %s: send the renewal quote",
                    format_date(self.env, self.renewal_date, date_format="d MMM"))
        if not lead.activity_ids.filtered(lambda a: a.summary == summary):
            lead.sudo().activity_schedule(
                "mail.mail_activity_data_todo", date_deadline=self.renewal_date,
                summary=summary, user_id=user.id)

    # -- the daily run ------------------------------------------------------

    @api.model
    def _cron_remind(self, today=None):
        """Once a day: the reminders due, the opportunities to open, the
        renewals given up. Each reminder is sent once, so a day missed or
        run twice never sends one twice."""
        today = today or fields.Date.context_today(self)
        due = self.search([
            ("state", "in", OPEN_STATES),
            ("renewal_date", "<=", today + relativedelta(days=max(REMINDER_DAYS))),
        ])
        for renewal in due:
            try:
                with self.env.cr.savepoint():
                    renewal._odin_remind(today)
            except Exception:  # one bad record must not stop the others
                _logger.exception("Renewal %s: reminder failed", renewal.id)
        return True

    def _odin_remind(self, today):
        self.ensure_one()
        days = (self.renewal_date - today).days
        if days <= -LAPSE_AFTER:
            self._odin_lapse()
            return
        sent = set(filter(None, (self.reminders_sent or "").split(",")))
        if days < 0:
            key = "overdue"
        else:
            key = str(min(t for t in REMINDER_DAYS if days <= t))
        if key in sent:
            return
        if self.state == "upcoming":
            lead = self._odin_ensure_opportunity()
            self.state = "in_progress"
            self._odin_schedule_todo(lead)
        self._odin_send_reminder(key, days, today)
        # Later reminders only: one missed is not sent late on top of the next.
        sent |= {str(t) for t in REMINDER_DAYS if key == "overdue" or t >= int(key)} | {key}
        self.reminders_sent = ",".join(sorted(sent))

    def _odin_send_reminder(self, key, days, today=None):
        self.ensure_one()
        template = self.env.ref("odin_medical_quote.mail_template_renewal_reminder",
                                raise_if_not_found=False)
        if not template:
            return
        people = self._odin_responsible()
        if key == "overdue":
            people |= self._odin_manager()
        partners = people.partner_id.filtered("email")
        if not partners:
            return
        template.with_context(odin_reminder=key, odin_days=days, odin_today=today).send_mail(
            self.id, email_values={
                "email_from": self._odin_mail_from(self.company_id),
                "recipient_ids": [(6, 0, partners.ids)],
            })
        when = (_("overdue") if key == "overdue" else _("on the day") if key == "0"
                else _("%s days before", key))
        self.message_post(body=_("Renewal reminder (%(when)s) emailed to %(who)s.",
                                 when=when, who=", ".join(partners.mapped("name"))))

    def _odin_lapse(self):
        self.ensure_one()
        self.state = "lapsed"
        lead = self.lead_id
        if lead and lead.active and lead.won_status != "won" and lead != self.source_lead_id:
            reason = self.env["crm.lost.reason"].sudo().search(
                [("name", "=", _("Not renewed"))], limit=1) or \
                self.env["crm.lost.reason"].sudo().create({"name": _("Not renewed")})
            lead.with_context(odin_renewal_lapse=True).action_set_lost(lost_reason_id=reason.id)
        self.message_post(body=_("Lapsed: not renewed within %s days of the renewal date.",
                                 LAPSE_AFTER))

    @api.model
    def _cron_digest(self, today=None):
        """Mondays: what renews in the next two weeks, what is overdue, and
        what was renewed or lost last week, to the company's manager."""
        today = today or fields.Date.context_today(self)
        for company in self.env["res.company"].search([("odin_renewal_digest_user_id", "!=", False)]):
            user = company.odin_renewal_digest_user_id
            if not user.active or not user.email:
                continue
            Renewal = self.with_company(company).with_context(allowed_company_ids=company.ids)
            domain = [("company_id", "=", company.id)]
            due = Renewal.search(domain + [
                ("state", "in", OPEN_STATES), ("renewal_date", ">=", today),
                ("renewal_date", "<=", today + relativedelta(days=DIGEST_AHEAD))])
            overdue = Renewal.search(domain + [
                ("state", "in", OPEN_STATES), ("renewal_date", "<", today)])
            closed = Renewal.search(domain + [
                ("state", "in", ("renewed", "lost", "lapsed")),
                ("write_date", ">=", fields.Datetime.now() - relativedelta(days=7))])
            if not (due or overdue or closed):
                continue
            body = self.env["ir.qweb"]._render("odin_medical_quote.renewal_digest", {
                "company": company, "due": due, "overdue": overdue, "closed": closed,
                "ahead": DIGEST_AHEAD, "base_url": company.get_base_url(),
                "format_date": lambda d: format_date(self.env, d, date_format="d MMM y"),
                "money": lambda r: formatLang(self.env, r.premium, currency_obj=r.currency_id)
                if r.premium else "",
            })
            self.env["mail.mail"].sudo().create({
                "subject": _("Renewals: %(due)s due in the next %(days)s days, %(overdue)s overdue",
                             due=len(due), days=DIGEST_AHEAD, overdue=len(overdue)),
                "email_from": self._odin_mail_from(company),
                "recipient_ids": [(6, 0, user.partner_id.ids)],
                "body_html": body,
                "auto_delete": True,
            })
        return True

    @api.model
    def _odin_mail_from(self, company):
        """``"BizSure" <noreply@odin.ist>``: the address every other app on
        the instance sends from. A company with an alias domain sends as its
        default sender; without one, the mail server's. The relay is
        authorised for odin.ist, not for an instance's own host name
        (bizsure.odin.ist), so an address on the host name moves up to it."""
        address = company.default_from_email
        if not address:
            Server = self.env["ir.mail_server"].sudo().with_company(company)
            relay = Server.search([], order="sequence, id", limit=1).from_filter \
                or Server._get_default_from_filter()
            parts = Server._parse_from_filter(relay)
            named = [part for part in parts if "@" in part]
            domains = [part for part in parts if "@" not in part]
            address = email_normalize(
                Server._get_default_from_address() or (named[0] if len(named) == 1 else ""))
            if address:
                local, domain = address.rsplit("@", 1)
                host = urlsplit(company.get_base_url()).hostname or ""
                if domain == host and host.count(".") >= 2:
                    address = "%s@%s" % (local, host.split(".", 1)[1])
            elif domains:
                address = "noreply@%s" % domains[0]
        if not address:
            return company.email_formatted
        return formataddr((company.name, address))

    # -- buttons --------------------------------------------------------------

    def action_odin_start_quote(self):
        """Quote the renewal: last year's quote again, ages a year on, for
        the renewal opportunity; or New quote for the client."""
        self.ensure_one()
        if self.state not in OPEN_STATES:
            raise UserError(_("This renewal is closed."))
        lead = self._odin_ensure_opportunity()
        if self.state == "upcoming":
            self.state = "in_progress"
        comparison = self.comparison_id
        if comparison and comparison.odin_builder_params and comparison.odin_from_builder:
            tag, name = comparison._odin_builder_tags()[comparison.odin_quote_type or "medical"]
            return {
                "type": "ir.actions.client",
                "tag": tag,
                "name": name,
                "params": {"lead_id": lead.id, "prefill_comparison_id": comparison.id},
            }
        return lead.action_odin_new_quote()

    def action_odin_open_lead(self):
        self.ensure_one()
        lead = self._odin_ensure_opportunity()
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.lead",
            "res_id": lead.id,
            "views": [(False, "form")],
        }

    def action_odin_renewed(self):
        """Renewed outside Cover Quotes: closed, and the next renewal made a
        period on."""
        for renewal in self.filtered(lambda r: r.state in OPEN_STATES):
            months = renewal.period_months or 12
            following = renewal.copy({
                "renewal_date": renewal.renewal_date + relativedelta(months=months),
                "cover_start": renewal.renewal_date,
                "state": "upcoming",
                "origin": "ours",
                "lead_id": False,
                "source_lead_id": renewal.lead_id.id or renewal.source_lead_id.id,
            })
            renewal.write({"state": "renewed", "next_renewal_id": following.id})
        return True

    def action_odin_lost(self):
        for renewal in self.filtered(lambda r: r.state in OPEN_STATES):
            renewal.state = "lost"
            lead = renewal.lead_id
            if lead and lead.active and lead.won_status != "won" and lead != renewal.source_lead_id:
                lead.with_context(odin_renewal_lapse=True).action_set_lost()
        return True

    def action_odin_reopen(self):
        self.filtered(lambda r: r.state in ("lost", "lapsed", "cancelled")).write(
            {"state": "upcoming", "reminders_sent": False})
        return True

    def action_odin_view_quotes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Renewal quotes"),
            "res_model": "odin.quote.comparison",
            "views": [(False, "list"), (False, "form")],
            "domain": [("id", "in", self.quote_ids.ids)],
        }

    # -- from the quote ---------------------------------------------------------

    @api.model
    def _odin_from_accept(self, comparison, order):
        """A client accepted ``order``: the renewal it closes is renewed, and
        the next renewal of what was sold is made (annual cover only)."""
        previous = comparison.odin_renewal_id or self.search([
            ("lead_id", "=", comparison.opportunity_id.id), ("state", "in", OPEN_STATES),
        ], limit=1) if comparison.opportunity_id else comparison.odin_renewal_id
        months = order._odin_policy_months()
        renewal = self.browse()
        if months >= 12 and not self.search_count([("order_id", "=", order.id)]):
            given = self.env.context.get("odin_cover_start")
            start = fields.Date.to_date(given) if given else fields.Date.context_today(self)
            renewal = self.create({
                "partner_id": order.partner_id.commercial_partner_id.id
                if order.partner_id.commercial_partner_id.is_company else order.partner_id.id,
                "line": comparison.odin_quote_type or "medical",
                "insurer_id": order._odin_insurer_partner().id,
                "product_id": order._odin_cover_product().id,
                "cover_start": start,
                "period_months": months,
                "start_to_confirm": not given,
                "renewal_date": start + relativedelta(months=months),
                "premium": order.amount_total,
                "user_id": (comparison.user_id or comparison.opportunity_id.user_id
                            or self.env.user).id,
                "origin": "ours",
                "source_lead_id": comparison.opportunity_id.id,
                "comparison_id": comparison.id,
                "order_id": order.id,
                "company_id": order.company_id.id,
            })
            comparison.message_post(body=Markup("%s %s") % (
                _("Renews on %s:", format_date(self.env, renewal.renewal_date,
                                               date_format="d MMM y")),
                renewal._get_html_link()))
        if previous and previous.state in OPEN_STATES:
            previous.write({"state": "renewed", "next_renewal_id": renewal.id or False})
        return renewal

    @api.model
    def _odin_link_quote(self, comparison):
        """A quote made for a renewal's opportunity is that renewal's quote."""
        if comparison.odin_renewal_id or not comparison.opportunity_id:
            return
        renewal = self.search([("lead_id", "=", comparison.opportunity_id.id),
                               ("state", "in", OPEN_STATES)], limit=1)
        if renewal:
            comparison.odin_renewal_id = renewal
            if renewal.state == "upcoming":
                renewal.state = "in_progress"


class CrmLead(models.Model):
    _inherit = "crm.lead"

    odin_renewal_ids = fields.One2many("odin.renewal", "lead_id", string="Renewals")
    odin_renewal_count = fields.Integer(compute="_compute_odin_renewal_count")

    @api.depends("odin_renewal_ids")
    def _compute_odin_renewal_count(self):
        for lead in self:
            lead.odin_renewal_count = len(lead.odin_renewal_ids)

    def action_odin_set_renewal(self):
        """The client's policy renews on a date: remember it, and remind the
        salesperson in time."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Set renewal date"),
            "res_model": "odin.renewal",
            "view_mode": "form",
            "views": [(self.env.ref("odin_medical_quote.view_renewal_form_quick").id, "form")],
            "target": "new",
            "context": {
                "default_lead_id": self.id,
                "default_partner_id": self.partner_id.id,
                "default_user_id": (self.user_id or self.env.user).id,
                "default_origin": "prospect",
                "default_company_id": self.company_id.id or self.env.company.id,
            },
        }

    def action_odin_view_renewals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Renewals"),
            "res_model": "odin.renewal",
            "views": [(False, "list"), (False, "form")],
            "domain": [("lead_id", "=", self.id)],
            "context": {"default_lead_id": self.id, "default_partner_id": self.partner_id.id},
        }

    def action_set_lost(self, **additional_values):
        result = super().action_set_lost(**additional_values)
        if not self.env.context.get("odin_renewal_lapse"):
            renewals = self.env["odin.renewal"].search(
                [("lead_id", "in", self.ids), ("state", "in", OPEN_STATES)])
            renewals.write({"state": "lost"})
        return result


class ResPartner(models.Model):
    _inherit = "res.partner"

    odin_renewal_count = fields.Integer(compute="_compute_odin_renewal_count")

    def _compute_odin_renewal_count(self):
        groups = self.env["odin.renewal"].sudo()._read_group(
            [("partner_id", "child_of", self.commercial_partner_id.ids)],
            ["partner_id"], ["__count"])
        counts = {partner.id: count for partner, count in groups}
        for partner in self:
            family = partner.commercial_partner_id | partner.commercial_partner_id.child_ids
            partner.odin_renewal_count = sum(counts.get(p.id, 0) for p in family)

    def action_odin_view_renewals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Renewals"),
            "res_model": "odin.renewal",
            "views": [(False, "list"), (False, "form")],
            "domain": [("partner_id", "child_of", self.commercial_partner_id.id)],
            "context": {"default_partner_id": self.id},
        }


class ResCompany(models.Model):
    _inherit = "res.company"

    odin_renewal_digest_user_id = fields.Many2one(
        "res.users", string="Weekly renewals summary to", domain=[("share", "=", False)],
        help="Mondays at 7:00: what renews in the next two weeks, what is overdue, what "
        "was renewed or lost. Overdue reminders are copied to them too.")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odin_renewal_digest_user_id = fields.Many2one(
        related="company_id.odin_renewal_digest_user_id", readonly=False)
