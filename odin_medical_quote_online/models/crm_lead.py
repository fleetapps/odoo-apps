from odoo import _, models
from odoo.exceptions import UserError


class CrmLead(models.Model):
    _inherit = "crm.lead"

    def action_odin_online_share(self):
        """Send the client an online quote page, filled in for them."""
        self.ensure_one()
        share = self.env["odin.medical.quote.online.share"]
        return {
            "type": "ir.actions.act_window",
            "name": _("Send quote link"),
            "res_model": share._name,
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_lead_id": self.id,
                        "default_quote_type": share._odin_type_for_lead(self)},
        }

    def action_odin_pick_up(self):
        """Assign to me: take an online quote waiting in the team's pool."""
        self.ensure_one()
        if self.user_id and self.user_id != self.env.user:
            raise UserError(_("%s has already picked this up.", self.user_id.name))
        if not self.user_id:
            self.user_id = self.env.user
        return True

    def write(self, vals):
        if not vals.get("user_id"):
            return super().write(vals)
        waiting = self.filtered(lambda lead: not lead.user_id)
        result = super().write(vals)
        if waiting:
            waiting._odin_online_picked_up()
        return result

    def _odin_online_picked_up(self):
        """Out of the pool -- by Assign to me, the Salesperson field or CRM's
        own assignment: its online quotes and their quotations become the
        salesperson's, with the to-do to contact the client."""
        comparisons = self.env["odin.quote.comparison"].sudo().search([
            ("opportunity_id", "in", self.ids), ("odin_channel", "=", "online"),
            ("user_id", "=", False), ("state", "in", ("draft", "sent"))])
        for lead in self:
            mine = comparisons.filtered(lambda c, lead=lead: c.opportunity_id == lead)
            if not mine:
                continue
            user = lead.user_id
            mine.user_id = user
            mine.plan_ids.order_id.filtered(
                lambda o: not o.user_id and o.state in ("draft", "sent")).user_id = user
            latest = mine.sorted("id")[-1]
            online = latest._odin_online().sudo()
            if latest.odin_callback_requested:
                summary = _("Call back %(name)s: %(how)s, %(when)s",
                            name=latest.partner_id.name,
                            how=dict(latest._fields["odin_contact_by"].selection).get(
                                latest.odin_contact_by, ""),
                            when=dict(latest._fields["odin_contact_when"].selection).get(
                                latest.odin_contact_when or "any", "").lower())
            else:
                summary = online._contact_summary(latest.partner_id, latest.odin_contact_by)
            online._schedule(lead, user, summary)
            lead.sudo().message_post(body=_("Picked up by %s.", user.name),
                                     message_type="comment", subtype_xmlid="mail.mt_note")
