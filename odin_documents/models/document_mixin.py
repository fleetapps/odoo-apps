from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round


class DocumentMixin(models.AbstractModel):
    """What every document template asks of the record it prints: the
    company, the document's terms, the bank accounts to pay into, amounts
    in words. Mixed into each model a document is printed from."""

    _name = "odin.document.mixin"
    _description = "Business document helpers"

    def action_doc_print(self):
        """A Print button: the document named in the button's context
        (doc_code), for these records."""
        code = self.env.context.get("doc_code")
        report = self._doc_type(code).report_id if code else False
        if not report:
            raise UserError(_("This document has no report to print: see Documents > Configuration."))
        return report.report_action(self)

    def _doc_company(self):
        company = self[:1].company_id if "company_id" in self._fields else self.env["res.company"]
        return (company or self.env.company).sudo()

    @api.model
    def _doc_type(self, code):
        return self.env["odin.document.type"].sudo()._get(code)

    def _doc_terms(self, code):
        return self._doc_type(code)._terms_for(self._doc_company())

    def _doc_acknowledgement(self, code):
        return self._doc_type(code)._acknowledgement_for(self._doc_company())

    def _doc_bank_accounts(self, currency=None):
        """The company's accounts in the document's currency (an account with
        no currency counts as the company's own); all of them if none is."""
        company = self._doc_company()
        banks = company.partner_id.bank_ids.sudo()
        if currency:
            matching = banks.filtered(
                lambda b: b.currency_id == currency or (not b.currency_id and currency == company.currency_id))
            if matching:
                return matching
        return banks

    @api.model
    def _doc_words(self, amount, currency):
        if not currency:
            return ""
        return (currency.amount_to_text(amount) or "").upper()

    def _doc_state_label(self):
        self.ensure_one()
        if "state" not in self._fields:
            return ""
        return dict(self._fields["state"]._description_selection(self.env)).get(self.state, "")

    @api.model
    def _doc_date(self, value):
        """dd/mm/yyyy; a date-time in the user's time zone, as Odoo shows it."""
        if not value:
            return ""
        if isinstance(value, datetime):
            value = fields.Datetime.context_timestamp(self, value)
        return value.strftime("%d/%m/%Y")

    @api.model
    def _doc_line_name(self, line):
        """The line's description without the [CODE] Odoo puts first: the code has its own column."""
        name = line.name or line.product_id.display_name or ""
        code = line.product_id.default_code
        prefix = f"[{code}] " if code else ""
        return name[len(prefix):] if prefix and name.startswith(prefix) else name

    @api.model
    def _doc_today(self):
        return fields.Date.context_today(self).strftime("%d/%m/%Y")

    @api.model
    def _doc_qty(self, value):
        """1,200 not 1,200.000; 2.5 stays 2.5."""
        text = f"{float_round(value or 0.0, precision_digits=3):,.3f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    @api.model
    def _doc_numbering(self, lines):
        """Line numbers for the item lines; sections and notes are not numbered."""
        numbers, count = {}, 0
        for line in lines:
            if not line.display_type or line.display_type == "product":
                count += 1
                numbers[line.id] = count
        return numbers

    @api.model
    def _doc_is_section(self, line):
        return bool(line.display_type) and line.display_type.startswith("line_section") \
            or line.display_type == "line_subsection"

    @api.model
    def _doc_letterhead(self, company):
        """The letterhead's lines under the name: short enough to stay in the header."""
        partner = company.partner_id
        street = ", ".join(p for p in (partner.street, partner.street2) if p)
        place = ", ".join(p for p in (" ".join(q for q in (partner.city, partner.zip) if q),
                                       partner.country_id.name) if p)
        pin = f"KRA PIN: {company.vat}" if company.vat else ""
        contact = " | ".join(p for p in (company.phone, company.email) if p)
        return [line for line in (street, place, pin, contact) if line]

    @api.model
    def _doc_address(self, partner):
        """The partner's address as the lines a letter prints."""
        if not partner:
            return []
        place = " ".join(p for p in (partner.city, partner.zip) if p)
        lines = [partner.street, partner.street2, place, partner.state_id.name,
                 partner.country_id.name if partner.country_id else False]
        return [line for line in lines if line]
