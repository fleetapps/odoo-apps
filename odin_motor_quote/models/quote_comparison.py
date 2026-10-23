from odoo import _, fields, models
from odoo.tools import formatLang


class QuoteComparison(models.Model):
    _inherit = "odin.quote.comparison"

    odin_quote_type = fields.Selection(
        selection_add=[("motor", "Motor")], ondelete={"motor": "set default"})

    def _odin_builder_tags(self):
        tags = super()._odin_builder_tags()
        tags["motor"] = ("odin_motor_quote", _("Add Motor Quote"))
        return tags

    def _odin_email_template(self):
        if self.odin_quote_type == "motor":
            return self.env.ref("odin_motor_quote.mail_template_motor_quote", raise_if_not_found=False)
        return super()._odin_email_template()

    def _odin_export_member_headings(self):
        if self.odin_quote_type == "motor":
            return _("Vehicle details"), _("The vehicle and the cover quoted")
        return super()._odin_export_member_headings()

    def _odin_export_members(self):
        """For a motor quote, the vehicle as entered."""
        self.ensure_one()
        if self.odin_quote_type != "motor":
            return super()._odin_export_members()
        cover = (self.odin_builder_params or {}).get("cover") or {}
        vehicle_class = self.env["odin.motor.class"].browse(int(cover.get("class_id") or 0)).exists()
        pairs = []
        if cover.get("registration"):
            pairs.append((_("Registration"), cover["registration"].strip().upper()))
        if cover.get("make_model"):
            pairs.append((_("Make and model"), cover["make_model"]))
        pairs.append((_("Vehicle type"), vehicle_class.name or ""))
        pairs.append((_("Cover"), _("Comprehensive") if cover.get("cover") != "tpo"
                      else _("Third party only")))
        if cover.get("cover") != "tpo" and cover.get("value"):
            pairs.append((_("Sum insured"), formatLang(self.env, float(cover["value"]), digits=0)))
        if cover.get("year"):
            pairs.append((_("Year of manufacture"), str(cover["year"])))
        if cover.get("seats"):
            pairs.append((_("Passengers"), str(cover["seats"])))
        if cover.get("tonnage"):
            pairs.append((_("Tonnage"), str(cover["tonnage"])))
        period = int(cover.get("period") or 12)
        if period != 12:
            pairs.append((_("Period"), _("1 month") if period == 1 else _("%s months", period)))
        return pairs
