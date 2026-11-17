from odoo import _, api, fields, models


class AccountMove(models.Model):
    """Invoices, receipts, credit notes and the export commercial invoice.

    An export invoice prints in two currencies. The rate is the one Odoo has
    for the invoice date (the CBK rate once the feed is on) and can be typed
    over, inline on the invoice, for a rate agreed with the customer. On an
    invoice in the foreign currency it is Odoo's own invoice rate, so the
    accounts use the rate the document prints.

    An invoice signed with KRA in another system, QuickBooks say, prints that
    system's number as its invoice number, with the CU invoice number eTIMS
    gave it; Odoo's own number is then printed as our reference."""

    _name = "account.move"
    _inherit = ["account.move", "odin.document.mixin"]

    doc_tax_invoice_no = fields.Char(
        string="Tax invoice no.", copy=False, tracking=True, index="btree_not_null",
        help="The number on the customer's tax invoice, from the system that signs it with KRA (QuickBooks, "
             "say). It prints as the invoice number, and Odoo's own number as our reference. Empty: Odoo's "
             "number is the invoice number.")
    doc_cu_invoice_no = fields.Char(
        string="CU invoice no.", copy=False, tracking=True, index="btree_not_null",
        help="The Control Unit invoice number KRA's eTIMS gave this invoice when it was signed. Printed on "
             "the invoice.")

    doc_is_export = fields.Boolean(
        string="Export", compute="_compute_doc_is_export", store=True,
        help="A customer invoice or credit note for a customer in another country: printed as a "
             "commercial invoice.")
    doc_foreign_currency_id = fields.Many2one(
        "res.currency", string="Second currency", compute="_compute_doc_foreign_currency", store=True,
        readonly=False, help="The other currency the commercial invoice prints.")
    doc_exchange_rate = fields.Float(
        string="Exchange rate", compute="_compute_doc_exchange_rate", inverse="_inverse_doc_exchange_rate",
        store=True, readonly=False, digits=(16, 4),
        help="Company currency for one unit of the second currency, e.g. 129.45 KES for 1 USD. Starts at "
             "Odoo's rate for the invoice date (CBK); type over it for a rate agreed with the customer. "
             "Changing the invoice date takes Odoo's rate again.")
    doc_goods_description = fields.Char(string="Description of goods", help="e.g. Liquid detergents.")

    @api.depends("move_type", "commercial_partner_id.country_id", "company_id.country_id")
    def _compute_doc_is_export(self):
        for move in self:
            country = move.commercial_partner_id.country_id
            home = move.company_id.country_id
            move.doc_is_export = bool(move.move_type in ("out_invoice", "out_refund") and country and home
                                      and country != home)

    @api.depends("currency_id", "company_id.currency_id", "company_id.doc_export_currency_id", "doc_is_export")
    def _compute_doc_foreign_currency(self):
        for move in self:
            company_currency = move.company_id.currency_id
            if move.currency_id and move.currency_id != company_currency:
                move.doc_foreign_currency_id = move.currency_id
            elif move.doc_is_export and move.company_id.doc_export_currency_id != company_currency:
                move.doc_foreign_currency_id = move.company_id.doc_export_currency_id
            else:
                move.doc_foreign_currency_id = False

    @api.depends("doc_foreign_currency_id", "currency_id", "invoice_currency_rate", "invoice_date", "date")
    def _compute_doc_exchange_rate(self):
        for move in self:
            foreign = move.doc_foreign_currency_id
            company_currency = move.company_id.currency_id
            if not foreign or foreign == company_currency:
                move.doc_exchange_rate = 0.0
            elif move.currency_id == foreign and move.invoice_currency_rate:
                move.doc_exchange_rate = 1.0 / move.invoice_currency_rate
            else:
                day = move.invoice_date or move.date or fields.Date.context_today(move)
                move.doc_exchange_rate = self.env["res.currency"]._get_conversion_rate(
                    foreign, company_currency, move.company_id, day)

    def _inverse_doc_exchange_rate(self):
        for move in self:
            in_foreign = move.currency_id == move.doc_foreign_currency_id != move.company_id.currency_id
            if in_foreign and move.doc_exchange_rate > 0 and move.state == "draft":
                move.invoice_currency_rate = 1.0 / move.doc_exchange_rate

    # ------------------------------------------------------------ templates
    def _doc_lines(self, with_notes=True):
        types = ("product", "line_section", "line_subsection", "line_note") if with_notes else ("product",)
        return self.invoice_line_ids.filtered(lambda line: line.display_type in types)

    def _doc_two_currencies(self, amount):
        """(company currency, second currency) of an amount in the invoice's currency."""
        self.ensure_one()
        company_currency = self.company_id.currency_id
        foreign = self.doc_foreign_currency_id
        rate = self.doc_exchange_rate or 0.0
        if self.currency_id == company_currency:
            return amount, (foreign.round(amount / rate) if foreign and rate else 0.0)
        return (company_currency.round(amount * rate) if rate else 0.0), amount

    def _doc_export_bank(self):
        """The account the customer pays into: the one in the export currency
        (by TT) before Odoo's default, which is the company's own currency."""
        self.ensure_one()
        currency = self.doc_foreign_currency_id or self.currency_id
        if self.partner_bank_id and self.partner_bank_id.currency_id == currency:
            return self.partner_bank_id
        banks = self._doc_bank_accounts(currency)
        return banks.filtered(lambda b: b.currency_id == currency)[:1] or self.partner_bank_id or banks[:1]

    def _doc_rate_text(self):
        """1 USD = 129.45 KES"""
        self.ensure_one()
        if not self.doc_foreign_currency_id or not self.doc_exchange_rate:
            return ""
        rate = f"{self.doc_exchange_rate:,.4f}".rstrip("0").rstrip(".")
        return f"1 {self.doc_foreign_currency_id.name} = {rate} {self.company_id.currency_id.name}"

    def _doc_conditions(self):
        self.ensure_one()
        if self.invoice_incoterm_id:
            text = f"{self.invoice_incoterm_id.code} - {self.invoice_incoterm_id.name}"
            return f"{text}, {self.incoterm_location}" if self.incoterm_location else text
        return self.company_id.doc_export_conditions or ""

    def _doc_kind(self):
        """TOTAL NO. & KIND: the cartons and pallets delivered, when known."""
        self.ensure_one()
        totals = self._doc_pickings()._doc_totals() if self._doc_pickings() else {}
        parts = []
        if totals.get("cartons"):
            parts.append(f"{totals['cartons']:,} cartons")
        if totals.get("pallets"):
            parts.append(f"{totals['pallets']:,} pallets")
        return ", ".join(parts) or "Various, as listed below"

    def _doc_orders(self):
        """Read with sudo: whoever prints the invoice need not open sales orders."""
        return self.sudo().invoice_line_ids.sale_line_ids.order_id

    def _doc_pickings(self):
        return self._doc_orders().picking_ids.filtered(
            lambda p: p.picking_type_code == "outgoing" and p.state == "done" and not p.return_id)

    def _doc_invoice_number(self):
        """The number the invoice prints as: its tax invoice's, else Odoo's once posted."""
        self.ensure_one()
        return self.doc_tax_invoice_no or (self.name if self.state == "posted" else "")

    def _doc_our_ref(self):
        """Odoo's own number, printed as our reference when the tax invoice's is the invoice number."""
        self.ensure_one()
        return self.name if self.doc_tax_invoice_no and self.state == "posted" else ""

    def _doc_order_ref(self):
        orders = self._doc_orders()
        refs = [ref for ref in orders.mapped("client_order_ref") if ref] or ([self.ref] if self.ref else [])
        # A tax invoice number kept as the order's reference too prints once: as the invoice number.
        taken = (self.doc_tax_invoice_no or "").strip().casefold()
        return ", ".join(ref for ref in refs if ref.strip().casefold() != taken)

    def _doc_weights(self):
        """(net, gross) kg: from the deliveries if done, else from the lines."""
        pickings = self._doc_pickings()
        if pickings:
            totals = pickings._doc_totals()
            return totals["net"], totals["gross"]
        net = gross = 0.0
        for line in self._doc_lines(with_notes=False).filtered("product_id"):
            qty = line.product_uom_id._compute_quantity(line.quantity, line.product_id.uom_id) \
                if line.product_uom_id else line.quantity
            net += qty * line.product_id.doc_net_weight
            gross += qty * line.product_id.weight
        return net, gross

    def _doc_payments(self):
        """The payments reconciled with the invoice, as the receipt lists them."""
        self.ensure_one()
        widget = self.invoice_payments_widget or {}
        payments = []
        for payment in widget.get("content", []):
            if payment.get("is_exchange"):
                continue
            payments.append({**payment, "date": fields.Date.to_date(payment.get("date"))})
        return sorted(payments, key=lambda p: p["date"] or fields.Date.today())

    def _doc_vat_label(self, line):
        return ", ".join(line.tax_ids.mapped("name")) or "0%"

    # ------------------------------------------------------------ the Band invoice
    def _doc_band_terms(self):
        """What the band prints under the company's PIN: the invoice's terms
        and, once KRA has signed it, its CU invoice number."""
        self.ensure_one()
        rows = [(_("Payment terms"), self.invoice_payment_term_id.name),
                (_("CU invoice no."), self.doc_cu_invoice_no)]
        return [(label, value) for label, value in rows if value]

    def _doc_band_meta(self):
        """The box under the title: the dates, then what else the invoice has."""
        self.ensure_one()
        rows = [(_("Invoice Date"), self._doc_date(self.invoice_date)),
                (_("Due Date"), self._doc_date(self.invoice_date_due)),
                (_("Order no."), self._doc_order_ref()),
                (_("Delivery note"), ", ".join(self._doc_pickings().mapped("name"))),
                (_("Customer no."), self.commercial_partner_id.ref),
                (_("Our ref."), self._doc_our_ref())]
        return [(label, value) for label, value in rows if value]

    def _doc_tax_rows(self):
        """(VAT 16%, amount) for each tax group, as Odoo totals the invoice."""
        self.ensure_one()
        totals = self.tax_totals or {}
        return [(group["group_name"], group["tax_amount_currency"])
                for subtotal in totals.get("subtotals", []) for group in subtotal.get("tax_groups", [])]


    def _get_action_with_base_document_layout_configurator(self, report_action):
        """Print on an invoice: the company's documents print on their own
        letterhead, so an administrator is not sent to Odoo's layout first."""
        if str(report_action.get("report_name", "")).startswith("odin_documents."):
            return report_action
        return super()._get_action_with_base_document_layout_configurator(report_action)


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    @api.model
    def _get_default_pdf_report_id(self, move):
        """Send & Print, the portal and the Print button use the company's
        document for the move, unless the customer or the journal names
        another: Odoo's own invoice gives way to it."""
        report = super()._get_default_pdf_report_id(move)
        if not move.company_id.doc_replace_standard:
            return report
        standard = self.env.ref("account.account_invoices", raise_if_not_found=False)
        ours = self.env["odin.document.type"]._invoice_reports()
        if report == standard or (report in ours and not move._is_action_report_available(report)):
            for candidate in ours:
                if move._is_action_report_available(candidate):
                    return candidate
        return report
