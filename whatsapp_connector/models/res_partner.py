from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    # A customer can reach the business without a visible phone number (R16):
    # the business-scoped user ID and username identify them then.
    wa_bsuid = fields.Char("WhatsApp BSUID", index="btree_not_null", copy=False)
    wa_username = fields.Char("WhatsApp Username", copy=False)
    wa_channel_ids = fields.One2many("discuss.channel", "wa_partner_id", string="WhatsApp Conversations")
    wa_channel_count = fields.Integer("WhatsApp", compute="_compute_wa_channel_count")

    @api.depends("wa_channel_ids")
    def _compute_wa_channel_count(self):
        for partner in self:
            partner.wa_channel_count = len(partner.sudo().wa_channel_ids)

    def action_wa_open_conversations(self):
        """SPEC.md §30: the contact's WhatsApp conversations."""
        self.ensure_one()
        return self.env["discuss.channel"]._wa_conversations_action([("wa_partner_id", "=", self.id)])

    @api.model
    def _wa_normalize_phone(self, digits_or_number):
        """E.164 ("+" and country code, as Meta wants it, R36) or False."""
        if not digits_or_number:
            return False
        number = str(digits_or_number).strip()
        if not number.startswith("+"):
            # Meta's wa_id / from are the full international number without "+"
            number = f"+{number.lstrip('0')}" if number.isdigit() else number
        return self._phone_format(number=number, force_format="E164") or False

    @api.model
    def _wa_find_or_create(self, bsuid=False, phone=False, name=False, username=False):
        """Find the customer's contact, or create it (SPEC.md §42, R16).

        BSUID first, then the normalized phone number. A contact may have no
        phone at all: customers with a WhatsApp username can arrive without one.
        """
        Partner = self.sudo().with_context(active_test=False)
        partner = self.browse()
        if bsuid:
            partner = Partner.search([("wa_bsuid", "=", bsuid)], order="id", limit=1)
        if not partner and phone:
            partner = Partner.search(
                [("phone_sanitized", "=", phone), ("active", "=", True)], order="id", limit=1,
            )
        if partner:
            vals = {}
            if bsuid and partner.wa_bsuid != bsuid:
                vals["wa_bsuid"] = bsuid
            if username and partner.wa_username != username:
                vals["wa_username"] = username
            if phone and not partner.phone:
                vals["phone"] = phone
            if vals:
                partner.write(vals)
            return partner
        return Partner.create({
            "name": name or (f"@{username}" if username else False) or phone or bsuid,
            "phone": phone or False,
            "wa_bsuid": bsuid or False,
            "wa_username": username or False,
        })
