import re

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round

from .stock_book import _escape_like

# A KRA PIN: A (an individual) or P (anyone else), nine digits and a letter.
KRA_PIN = re.compile(r"[AP][0-9]{9}[A-Z]")


class StockBookSale(models.Model):
    """One customer invoice entered in the sale window: the documents its
    book lines became, so stock, revenue and cost of sales go together."""

    _name = "odin.stock.sale"
    _description = "Stock Book sale"
    _order = "date desc, id desc"
    _rec_name = "sale_ref"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, check_company=True)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True)
    sale_ref = fields.Char(string="Invoice no.", required=True)
    cu_invoice_no = fields.Char(
        string="CU invoice no.", copy=False,
        help="The Control Unit invoice number KRA's eTIMS gave the invoice when it was signed: printed on the "
             "invoice.")
    payment_term_id = fields.Many2one(
        "account.payment.term", string="Payment terms", check_company=True,
        help="The invoice's terms, from which Odoo dates it due. Empty: the customer's, or due on the day.")
    date = fields.Date(required=True)
    pay_journal_id = fields.Many2one("account.journal", string="Paid into", check_company=True,
                                     domain=[("type", "in", ("bank", "cash"))],
                                     help="Empty: not paid yet, the invoice stays open.")
    line_ids = fields.One2many("odin.stock.line", "stock_sale_id", string="Stock lines")
    charge_ids = fields.One2many("odin.stock.sale.charge", "sale_id", string="Other charges")
    order_id = fields.Many2one("sale.order", readonly=True, copy=False)
    invoice_id = fields.Many2one("account.move", readonly=True, copy=False)
    payment_id = fields.Many2one("account.payment", readonly=True, copy=False)
    credit_note_id = fields.Many2one("account.move", string="Credit note", readonly=True, copy=False)
    undone_on = fields.Datetime(readonly=True, copy=False)
    undone_by_id = fields.Many2one("res.users", readonly=True, copy=False)

    def unlink(self):
        if self.filtered("order_id"):
            raise UserError(_("This sale is posted. Reverse it with a credit note in Accounting."))
        return super().unlink()

    def _undo(self):
        """The whole sale taken back at once, in the order Odoo requires: the
        payment cancelled, the invoice credited in full (a sales invoice is
        reversed with a credit note, never cancelled), the goods back into
        stock, the order cancelled. All of it or none. Each document stays,
        and the goods coming back are on the movement log."""
        self.ensure_one()
        if self.undone_on:
            raise UserError(_("%s is already undone.", self.sale_ref))
        order = self.order_id.sudo()
        if not order:
            raise UserError(_("This sale is not posted yet."))
        invoice, payment = self.invoice_id.sudo(), self.payment_id.sudo()
        if payment.is_matched:
            raise UserError(_("%(payment)s is matched with a bank statement line. Undo that match in Accounting first.",
                              payment=payment.display_name))
        if (order.invoice_ids - invoice).filtered(lambda m: m.state != "cancel"):
            raise UserError(_("%(order)s has another invoice or credit note. Undo it in Sales and Accounting.",
                              order=order.name))
        stock = order.order_line.filtered(lambda l: l.product_id.is_storable)
        if any(l.product_uom_id.compare(l.qty_delivered, l.product_uom_qty) for l in stock):
            raise UserError(_("Part of %(order)s was already returned. Undo the rest in Sales and Accounting.",
                              order=order.name))
        if payment:
            payment.action_draft()
            payment.action_cancel()
        if invoice:
            if invoice.payment_state not in ("not_paid", "invoicing_legacy"):
                raise UserError(_("%(invoice)s has another payment on it. Remove it in Accounting first.",
                                  invoice=invoice.display_name))
            self.credit_note_id = self._credit(invoice)
        for picking in order.picking_ids.filtered(lambda p: p.state == "done" and p.picking_type_id.code == "outgoing"):
            self.env["odin.stock.line"]._return_moves(picking.move_ids.filtered(lambda m: m.state == "done"))
        if order.locked:
            order.action_unlock()
        order.action_cancel()
        self.write({"undone_on": fields.Datetime.now(), "undone_by_id": self.env.user.id})

    def _credit(self, invoice):
        """Odoo's Reverse on the invoice, a full refund: the credit note posted
        today and matched with the invoice, which then reads Reversed."""
        wizard = self.env["account.move.reversal"].sudo().with_company(self.company_id).with_context(
            active_model="account.move", active_ids=invoice.ids,
        ).create({"journal_id": invoice.journal_id.id, "date": fields.Date.context_today(self),
                  "reason": _("Undone in the Stock Book")})
        credit = invoice._reverse_moves([wizard._prepare_default_reversal(invoice)], cancel=True)
        if credit.state != "posted" or invoice.payment_state != "reversed":
            raise UserError(_("%(invoice)s could not be credited in full. Nothing was undone.",
                              invoice=invoice.display_name))
        return credit

    def _deliver(self, line):
        """The moves of one book line, making the documents the first time.
        Odoo's own sale flow: the delivery comes from the order, and the
        invoice takes its cost of sales from that delivery."""
        self.ensure_one()
        if not self.order_id:
            self._make_documents()
        return line.sale_line_id.sudo().move_ids.filtered(lambda m: m.state == "done").sudo(False)

    def _make_documents(self):
        warehouse = self.warehouse_id
        if warehouse.delivery_steps != "ship_only":
            raise UserError(_("%(warehouse)s delivers in several steps, so goods would not leave stock from here. "
                              "Deliver this sale in Sales.", warehouse=warehouse.display_name))
        lines = self.line_ids.filtered(lambda l: l.state == "draft").sorted("id")
        for line in lines:
            line.partner_id = self.partner_id
        # The terms the window had, else the customer's now (a sale saved before it had any).
        terms = self.payment_term_id or self.partner_id.sudo().with_company(self.company_id).property_payment_term_id
        order = self.env["sale.order"].sudo().with_company(self.company_id).create({
            "partner_id": self.partner_id.id, "company_id": self.company_id.id, "warehouse_id": warehouse.id,
            "client_order_ref": self.sale_ref, "user_id": self.env.user.id, "payment_term_id": terms.id,
            "date_order": lines[:1]._when(),
            "order_line": [Command.create({
                "product_id": line.product_id.id, "product_uom_qty": line.qty_out,
                "product_uom_id": line.product_id.uom_id.id, "price_unit": line.unit_price,
                # Always stated: left out, Odoo takes the item's default tax.
                "tax_ids": [Command.set(line.tax_id.ids)],
            }) for line in lines] + [Command.create({
                "product_id": charge.product_id.id, "name": charge.name or charge.product_id.display_name,
                "product_uom_qty": charge.qty, "product_uom_id": charge.product_id.uom_id.id,
                "price_unit": charge.unit_price, "tax_ids": [Command.set(charge.tax_id.ids)],
            }) for charge in self.charge_ids],
        })
        for line, order_line in zip(lines, order.order_line):
            line.sale_line_id = order_line.id
        when = lines[:1]._when()
        order.action_confirm()
        # Confirming dates the order today: it keeps the day of the invoice.
        order.date_order = when
        picking = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
        if len(picking) != 1:
            raise UserError(_("%(order)s did not make one delivery. Deliver it in Sales.", order=order.display_name))
        lines[:1]._validate_picking(picking)
        lines.write({"picking_id": picking.id})
        # What is not stock went with the goods: invoiced in full under any policy.
        for order_line in order.order_line.filtered(lambda l: l.qty_delivered_method == "manual"):
            order_line.qty_delivered = order_line.product_uom_qty
        self.write({"order_id": order.id})
        if order.currency_id.is_zero(order.amount_total):
            # Free goods, a replacement: delivered, with nothing to invoice.
            order.partner_id._increase_rank("customer_rank")
            return
        invoice = order._create_invoices()
        if len(invoice) != 1:
            raise UserError(_("%(order)s did not make one invoice. Nothing was saved.", order=order.display_name))
        # The invoice is numbered as typed in Invoice no. (QuickBooks's), not by Odoo's sequence.
        number = " ".join((self.sale_ref or "").split())
        self._free_invoice_number(invoice.journal_id, number)
        values = {"invoice_date": self.date, "date": self.date, "ref": self.sale_ref, "name": number}
        # With Business Documents: the numbers the invoice prints, QuickBooks's as its invoice number.
        if "doc_tax_invoice_no" in invoice._fields:
            values["doc_tax_invoice_no"] = self.sale_ref
        if "doc_cu_invoice_no" in invoice._fields and self.cu_invoice_no:
            values["doc_cu_invoice_no"] = self.cu_invoice_no
        invoice.write(values)
        if invoice.name != number or not invoice._sequence_matches_date():
            # Odoo refuses a number that reads as a dated sequence of another
            # date (2025/014 on a 2026 invoice, say).
            raise UserError(_("Odoo would not number the invoice %(number)s: it reads it as a number of another "
                              "date. Nothing was saved.", number=number))
        if not invoice.invoice_payment_term_id:
            # With terms, Odoo dates it due from the invoice date. Without, it
            # keeps the day the invoice was made: due on its own date instead.
            invoice.invoice_date_due = self.date
        if invoice.currency_id.compare_amounts(invoice.amount_total, order.amount_total):
            raise UserError(_("The invoice for %(order)s comes to %(invoice)s, the order to %(total)s. Nothing was "
                              "saved.", order=order.display_name, invoice=invoice.amount_total,
                              total=order.amount_total))
        invoice.action_post()
        if invoice.name != number:
            # Odoo drops a number that reads as a dated sequence of another
            # date (2025/014 on a 2026 invoice, say).
            raise UserError(_("Odoo would not number the invoice %(number)s: it reads it as a number of another "
                              "date. Nothing was saved.", number=number))
        self.invoice_id = invoice
        if self.pay_journal_id:
            self.payment_id = self._receive(invoice).id

    def _free_invoice_number(self, journal, number):
        """The number typed must be free in the journal. An undone sale gives
        its invoice's number back: that invoice, credited in full, is renamed
        "<number> (undone)" so the sale can be entered again under it."""
        Move = self.env["account.move"].sudo()
        taken = Move.search([("journal_id", "=", journal.id), ("state", "=", "posted"), ("name", "=", number)])
        if not taken:
            return
        undone = self.sudo().search([("invoice_id", "in", taken.ids), ("undone_on", "!=", False)]).invoice_id
        for move in undone:
            name, count = _("%s (undone)", number), 1
            while Move.search_count([("journal_id", "=", journal.id), ("name", "=", name)]):
                count += 1
                name = _("%(number)s (undone %(count)s)", number=number, count=count)
            move.name = name
        other = taken - undone
        if other:
            raise UserError(_("Invoice no. %(number)s is already the number of the invoice for %(customer)s. Nothing "
                              "was saved.", number=number, customer=other[:1].partner_id.display_name))

    def _receive(self, invoice):
        """Paid in full into the journal chosen, and checked to be in the ledger."""
        journal = self.pay_journal_id.sudo()
        wizard = self.env["account.payment.register"].sudo().with_company(self.company_id).with_context(
            active_model="account.move", active_ids=invoice.ids,
        ).create({"journal_id": journal.id, "payment_date": self.date})
        if not wizard.payment_method_line_id:
            raise UserError(_("%(journal)s has no incoming payment method. Add one on the journal, or save this sale "
                              "as not paid yet.", journal=journal.display_name))
        payment = wizard._create_payments()
        if not payment.move_id or invoice.payment_state not in ("in_payment", "paid"):
            raise UserError(_("The payment into %(journal)s did not reach the accounts. Nothing was saved. Save this "
                              "sale as not paid yet and record the payment in Accounting.",
                              journal=journal.display_name))
        return payment


class StockBookSaleCharge(models.Model):
    """A charge on the invoice that is not stock: delivery, say."""

    _name = "odin.stock.sale.charge"
    _description = "Stock Book sale: other charge"
    _order = "id"

    sale_id = fields.Many2one("odin.stock.sale", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", required=True, domain=[("is_storable", "=", False)])
    name = fields.Char(string="Description")
    qty = fields.Float(digits="Product Unit", required=True)
    unit_price = fields.Float(digits="Product Price", required=True)
    tax_id = fields.Many2one("account.tax", string="VAT")


class StockBook(models.AbstractModel):
    _inherit = "odin.stock.book"

    @api.model
    def _sale_taxes(self):
        return self.env["account.tax"].search([
            ("type_tax_use", "=", "sale"), ("company_id", "=", self.env.company.id), ("amount", ">=", 0)],
            order="sequence, id")

    @api.model
    def _sale_pay_journals(self):
        journals = self.env["account.journal"].sudo().search(
            [("company_id", "=", self.env.company.id), ("type", "in", ("bank", "cash"))], order="sequence, id")
        return journals.filtered(lambda j: j.inbound_payment_method_line_ids[:1].payment_account_id)

    @api.model
    def book_sale_options(self):
        journals = self._sale_pay_journals()
        names = journals.mapped("name")
        return {
            "journals": [{"id": j.id, "label": f"{j.name} ({j.code})" if names.count(j.name) > 1 else j.name}
                         for j in journals],
            "taxes": [{"id": t.id, "label": _("%s (incl.)", t.name) if t.price_include and t.amount else t.name}
                      for t in self._sale_taxes()],
            "payment_terms": [{"id": t.id, "label": t.name} for t in self._sale_payment_terms()],
            "today": fields.Date.to_string(fields.Date.context_today(self)),
            "can_post": self._is_supervisor(), "currency": self.env.company.currency_id.name,
        }

    # ------------------------------------------------------------ the customer's PIN and terms
    @api.model
    def _sale_payment_terms(self):
        """The terms a sales order of this company can take, as Sales lists them."""
        return self.env["account.payment.term"].sudo().search(
            [("company_id", "in", (False, self.env.company.id))], order="sequence, id")

    @api.model
    def _sale_customer(self, partner):
        """The customer as the sale window shows it: its KRA PIN and terms, to fill in where missing."""
        commercial = partner.sudo().commercial_partner_id.with_company(self.env.company)
        return {"id": partner.id, "name": partner.name, "vat": commercial.vat or "",
                "payment_term_id": commercial.property_payment_term_id.id or False}

    @api.model
    def _sale_pin(self, text, partner=None):
        """The KRA PIN typed, as KRA writes it: P052112729P. One the customer
        already has is kept as it is, a foreign tax ID say; a new one must be
        a KRA PIN. Empty: nothing typed."""
        pin = "".join((text or "").split()).upper()
        known = "".join((partner.sudo().commercial_partner_id.vat or "").split()).upper() if partner else ""
        if pin and pin != known and not KRA_PIN.fullmatch(pin):
            raise UserError(_("%s is not a KRA PIN: A or P, nine digits and a letter, e.g. P052112729P. A customer "
                              "outside Kenya keeps its tax ID in Sales > Customers.", text.strip()))
        return pin

    @api.model
    def _sale_terms(self, term_id):
        if not term_id:
            return self.env["account.payment.term"]
        terms = self._sale_payment_terms().filtered(lambda t: t.id == term_id)
        if not terms:
            raise UserError(_("Choose the payment terms from the list."))
        return terms

    @api.model
    def _sale_update_customer(self, partner, pin, terms):
        """What was typed for a customer goes on the customer, for every sale
        after: a PIN or terms it lacked, or a correction. Left empty, what the
        customer has stays."""
        commercial = partner.sudo().commercial_partner_id.with_company(self.env.company)
        values = {}
        if pin and pin != "".join((commercial.vat or "").split()).upper():
            values["vat"] = pin
        if terms and terms != commercial.property_payment_term_id:
            values["property_payment_term_id"] = terms.id
        if values:
            commercial.write(values)

    @api.model
    def book_sale_due(self, payment_term_id=False, date=False):
        """The day an invoice of this date falls due on these terms, as Odoo
        dates it: its last instalment. No terms: the invoice date."""
        day = fields.Date.to_date(date) or fields.Date.context_today(self)
        terms = self._sale_payment_terms().filtered(lambda t: t.id == payment_term_id)
        due = max((line._get_due_date(day) for line in terms.line_ids), default=day)
        return fields.Date.to_string(due)

    @api.model
    def book_sale_customers(self, text):
        name = " ".join((text or "").split())
        if not name:
            return {"items": [], "can_create": False}
        partners = self.env["res.partner"].search([("name", "ilike", name), ("parent_id", "=", False)],
                                                  order="customer_rank desc, name", limit=8)
        exact = partners.filtered(lambda p: p.name.casefold() == name.casefold())
        return {"items": [self._sale_customer(p) for p in partners], "can_create": not exact}

    @api.model
    def book_sale_products(self, text, warehouse_id=False):
        result = [{**item, "stock": True} for item in self.book_suggest_products(text, warehouse_id)]
        domain = [("is_storable", "=", False), ("sale_ok", "=", True)]
        for word in (text or "").split():
            domain += ["|", ("default_code", "ilike", word), ("name", "ilike", word)]
        if (text or "").split():
            result += [{**self._product(p), "stock": False, "tracked": False, "onhand": 0.0}
                       for p in self.env["product.product"].search(domain, limit=4, order="name")]
        return result

    @api.model
    def book_sale_item(self, product_id, partner_id=False, qty=0.0, date=False):
        """The item's sales VAT; its price for this customer, as a sales order
        in Odoo would price it (the customer's pricelist, at this quantity and
        on this day); and what the customer last paid, as a hint. The window
        puts the price in and the person can type over it."""
        product = self.env["product.product"].browse(product_id).exists()
        company = self.env.company
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        tax = product.taxes_id.filtered(lambda t: t.company_id == company)[:1] & self._sale_taxes()
        last = self.env["sale.order.line"].sudo().search(
            [("company_id", "=", company.id), ("state", "=", "sale"), ("product_id", "=", product.id)]
            + ([("order_partner_id", "=", partner.id)] if partner else []), order="id desc", limit=1)
        # Storekeepers price the sale without being sales users, as they post
        # it: read the pricelists and fiscal positions with sudo.
        book = self.sudo()
        pricelist = book._sale_pricelist(partner.sudo())
        return {"tax_id": tax.id or False, "last_cost": last.price_unit or product.list_price,
                "price": book._sale_price(product.sudo(), partner.sudo(), pricelist, qty, date),
                "pricelist": pricelist.name or ""}

    @api.model
    def _sale_pricelist(self, partner):
        """The pricelist a sales order for this customer takes; for a customer
        not created yet, the one a new customer gets: the first one."""
        company = self.env.company
        if partner:
            return partner.with_company(company).property_product_pricelist
        Pricelist = self.env["product.pricelist"]
        if not self.env["res.groups"]._is_feature_enabled("product.group_product_pricelist"):
            return Pricelist
        return Pricelist.search(Pricelist._get_partner_pricelist_multi_search_domain_hook(company.id), limit=1)

    @api.model
    def _sale_price(self, product, partner, pricelist, qty, date):
        """The unit price as sale.order.line prices it: the pricelist's rule
        for the product, quantity (minimum quantities apply), unit and day,
        then fitted to the customer's fiscal position (its VAT) as
        _reset_price_unit does. With no pricelist: the Sales Price."""
        if not product:
            return 0.0
        company = self.env.company
        product = product.with_company(company)
        day = fields.Date.to_date(date) or fields.Date.context_today(self)
        price = pricelist._get_product_price(product, qty or 1.0, currency=company.currency_id,
                                             uom=product.uom_id, date=day)
        fiscal_position = self.env["account.fiscal.position"].with_company(company)._get_fiscal_position(partner) \
            if partner else self.env["account.fiscal.position"]
        price = product._get_tax_included_unit_price_from_price(
            price, product_taxes=product.taxes_id._filter_taxes_by_company(company), fiscal_position=fiscal_position)
        return float_round(price, precision_digits=self.env["decimal.precision"].precision_get("Product Price"))

    @api.model
    def _check_sale_ref(self, partner, ref, ignore_ids=None):
        ref = " ".join((ref or "").split())
        if not ref or not partner:
            return
        company = self.env.company
        pattern = _escape_like(ref)
        order = self.env["sale.order"].sudo().search([
            ("company_id", "=", company.id), ("partner_id", "=", partner.id), ("state", "!=", "cancel"),
            ("client_order_ref", "=ilike", pattern)], limit=1)
        if order:
            raise UserError(_("%(ref)s for %(customer)s is already in Sales as %(order)s.",
                              ref=ref, customer=partner.display_name, order=order.name))
        waiting = self.env["odin.stock.line"].search(
            [("state", "=", "draft"), ("reason", "=", "sale"), ("company_id", "=", company.id),
             ("partner_id", "=", partner.id), ("purchase_ref", "=ilike", pattern),
             ("id", "not in", ignore_ids or [])], limit=1)
        if waiting:
            raise UserError(_("%(ref)s for %(customer)s is already on the book, waiting to be posted.",
                              ref=ref, customer=partner.display_name))
        # The number typed becomes the invoice's number: free in the sales
        # journals, but for an undone sale's, which gives it back.
        Move = self.env["account.move"].sudo()
        taken = Move.search([("company_id", "=", company.id), ("journal_id.type", "=", "sale"),
                             ("state", "=", "posted"), ("name", "=", ref)])
        taken -= self.env["odin.stock.sale"].sudo().search(
            [("invoice_id", "in", taken.ids), ("undone_on", "!=", False)]).invoice_id
        if taken:
            raise UserError(_("Invoice no. %(ref)s is already the number of the invoice for %(customer)s.",
                              ref=ref, customer=taken[:1].partner_id.display_name))

    @api.model
    def _check_sale_cu(self, cu):
        """A CU invoice number is KRA's for one invoice: typed in once. The
        sale being edited was taken off before this is asked. An undone sale
        gives its number back, and its invoice, credited in full, with it:
        the same signed invoice is entered again, corrected."""
        if not cu:
            return
        company = self.env.company
        pattern = _escape_like(cu)
        sales = self.env["odin.stock.sale"].sudo().search(
            [("company_id", "=", company.id), ("cu_invoice_no", "=ilike", pattern)])
        sale = sales.filtered(lambda s: not s.undone_on)[:1]
        if sale:
            raise UserError(_("CU invoice no. %(cu)s is already on invoice %(ref)s for %(customer)s.",
                              cu=cu, ref=sale.sale_ref, customer=sale.partner_id.display_name))
        Move = self.env["account.move"].sudo()
        if "doc_cu_invoice_no" in Move._fields:
            move = Move.search([("company_id", "=", company.id), ("state", "!=", "cancel"),
                                ("doc_cu_invoice_no", "=ilike", pattern), ("id", "not in", sales.invoice_id.ids)],
                               limit=1)
            if move:
                raise UserError(_("CU invoice no. %(cu)s is already on %(invoice)s.",
                                  cu=cu, invoice=move.display_name))

    @api.model
    def book_sale_check(self, partner_id, ref, ignore_ids=None):
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        try:
            self._check_sale_ref(partner, ref, ignore_ids)
        except UserError as error:
            return str(error)
        return False

    @api.model
    def book_sale_totals(self, rows, partner_id=False):
        Tax = self.env["account.tax"]
        Product = self.env["product.product"]
        company = self.env.company
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        allowed = self._sale_taxes()
        base_lines = [Tax._prepare_base_line_for_taxes_computation(
            None, price_unit=row.get("cost") or 0.0, quantity=row.get("qty") or 0.0,
            tax_ids=allowed.filtered(lambda t: t.id == row.get("tax_id")),
            product_id=Product.browse(row.get("product_id") or []).exists(), partner_id=partner,
            currency_id=company.currency_id, rate=1.0,
        ) for row in rows]
        Tax._add_tax_details_in_base_lines(base_lines, company)
        Tax._round_base_lines_tax_details(base_lines, company)
        summary = Tax._get_tax_totals_summary(base_lines=base_lines, currency=company.currency_id, company=company)
        return {
            "lines": [line["tax_details"]["total_excluded_currency"] for line in base_lines],
            "subtotal": summary["base_amount_currency"], "vat": summary["tax_amount_currency"],
            "total": summary["total_amount_currency"],
        }

    @api.model
    def book_sale_load(self, line_id):
        """A draft sale line for the sale window: all of its invoice if it came
        from the window, only itself otherwise, a draft kept with Save draft
        included. Saving replaces what is loaded."""
        Line = self.env["odin.stock.line"]
        line = Line.browse(line_id).exists()
        if not line or line.state != "draft" or line.reason != "sale":
            raise UserError(_("Only a sale not yet posted can be opened here."))
        window = line.stock_sale_id
        # In the order typed: the book lists the newest first.
        lines = window.line_ids.filtered(lambda l: l.state == "draft").sorted("id") if window else line
        partner = window.partner_id or line.partner_id
        name = (line.partner_name or "").strip()
        if not partner and name:
            partner = self.env["res.partner"].search(
                [("name", "=ilike", _escape_like(name)), ("parent_id", "=", False)], limit=1)
        rows = [{"product": {**self._product(l.product_id), "stock": True, "tracked": l.product_id.tracking != "none"},
                 "qty": l.qty_out, "cost": l.unit_price, "tax_id": l.tax_id.id or False, "note": l.note or ""}
                for l in lines]
        rows += [{"product": {**self._product(c.product_id), "stock": False, "tracked": False}, "qty": c.qty,
                  "cost": c.unit_price, "tax_id": c.tax_id.id or False, "note": c.name or ""}
                 for c in window.charge_ids]
        customer = self._sale_customer(partner) if partner else {}
        return {
            "supplier": customer or False,
            "supplier_query": partner.name if partner else name.title(),
            "purchase_ref": line.purchase_ref or "", "date": fields.Date.to_string(line.date),
            "paid": (str(window.pay_journal_id.id) if window.pay_journal_id else "none") if window else "",
            "lines": rows, "replace_ids": lines.ids, "edit": bool(window),
            # As saved, else the customer's: what the window shows for them.
            "partner_vat": customer.get("vat", ""),
            "payment_term_id": window.payment_term_id.id or customer.get("payment_term_id") or False,
            "cu_invoice_no": window.cu_invoice_no or "",
        }

    @api.model
    def book_sale_undo(self, line_id):
        """A saved sale taken back in one step, and what it was, so it can be
        entered again correctly, at its prices."""
        if not self._is_supervisor():
            raise UserError(_("Only a Stock Book supervisor can undo a sale."))
        line = self.env["odin.stock.line"].browse(line_id).exists()
        window = line.stock_sale_id
        if not window:
            raise UserError(_("Only a sale made with Add sale can be undone here."))
        posted = window.line_ids.filtered(lambda l: l.state == "posted").sorted("id")
        rows = [{"product": {**self._product(l.product_id), "stock": True, "tracked": False}, "qty": l.qty_out,
                 "cost": l.unit_price, "tax_id": l.tax_id.id or False, "note": l.note or ""} for l in posted]
        rows += [{"product": {**self._product(c.product_id), "stock": False, "tracked": False}, "qty": c.qty,
                  "cost": c.unit_price, "tax_id": c.tax_id.id or False, "note": c.name or ""}
                 for c in window.charge_ids]
        customer = self._sale_customer(window.partner_id)
        again = {
            "supplier": customer,
            "supplier_query": window.partner_id.name, "purchase_ref": window.sale_ref,
            "date": fields.Date.to_string(window.date),
            "paid": str(window.pay_journal_id.id) if window.pay_journal_id else "none",
            "lines": rows, "replace_ids": [], "edit": False, "keep_prices": True,
            # The KRA PIN, terms and CU number it was saved with, as book_sale_load gives them.
            "partner_vat": customer["vat"],
            "payment_term_id": window.payment_term_id.id or customer["payment_term_id"] or False,
            "cu_invoice_no": window.cu_invoice_no or "",
        }
        order = window.order_id.sudo().name
        window._undo()
        return {"order": order, "purchase_ref": window.sale_ref, "again": again,
                "credit_note": window.credit_note_id.sudo().name or ""}

    @api.model
    def _sale_replace(self, ids):
        lines = self.env["odin.stock.line"].browse(ids or []).exists()
        if lines.filtered(lambda l: l.state != "draft" or l.reason != "sale"):
            raise UserError(_("These lines were posted meanwhile. Nothing was saved: check the book."))
        windows = lines.stock_sale_id
        everything = lines | windows.line_ids
        if everything.filtered(lambda l: l.state == "posted"):
            raise UserError(_("These lines were posted meanwhile. Nothing was saved: check the book."))
        everything.unlink()
        windows.sudo().unlink()

    @api.model
    def book_sale_save(self, values, post=True, replace_ids=None):
        """One invoice from the sale window, as Stock Book lines: posted at once
        by a supervisor, left for one otherwise. All of it or none.
        ``post=False``: kept as a draft, as typed, to finish and deliver
        later (Save draft). Nothing is delivered or invoiced yet.
        ``replace_ids``: the draft being saved again, which this replaces."""
        Line = self.env["odin.stock.line"]
        company = self.env.company
        self._sale_replace(replace_ids)
        wh = self._warehouse(values.get("warehouse_id"))
        if not wh:
            raise UserError(_("Create a warehouse first."))
        ref = " ".join((values.get("purchase_ref") or "").split())
        if not ref:
            raise UserError(_("Type the invoice number."))
        partner = self.env["res.partner"].browse(values.get("partner_id") or []).exists()
        name = " ".join((values.get("partner_name") or "").split())
        if not partner and not name:
            raise UserError(_("Pick the customer."))
        day = fields.Date.to_date(values.get("date"))
        if not day:
            raise UserError(_("Type the date."))
        if day > fields.Date.context_today(self):
            raise UserError(_("The delivery date cannot be in the future: %s.", day.strftime("%d %b %Y")))
        rows = values.get("lines") or []
        if not rows:
            raise UserError(_("Add at least one item."))
        allowed = self._sale_taxes()
        journal = self.env["account.journal"].sudo().browse(values.get("pay_journal_id") or []).exists()
        if journal and journal not in self._sale_pay_journals():
            raise UserError(_("Choose where it was paid into."))
        pin = self._sale_pin(values.get("partner_vat"), partner)
        terms = self._sale_terms(values.get("payment_term_id"))
        cu = " ".join((values.get("cu_invoice_no") or "").split())
        vals_list, charges = [], []
        for number, row in enumerate(rows, 1):
            product = self.env["product.product"].browse(row.get("product_id") or []).exists()
            if not product or not (product.is_storable or product.sale_ok):
                raise UserError(_("Line %s: pick an item that can be sold.", number))
            if product.tracking != "none":
                raise UserError(_("%s is tracked by lot or serial number. Deliver it in Sales, where the numbers are "
                                  "entered.", product.display_name))
            if (row.get("qty") or 0) <= 0:
                raise UserError(_("%s: type the quantity.", product.display_name))
            if row.get("cost") is None or row["cost"] < 0:
                raise UserError(_("%s: type the price per unit, or 0 if it is free.", product.display_name))
            if row.get("tax_id") not in allowed.ids:
                raise UserError(_("%s: choose the VAT.", product.display_name))
            if not product.is_storable:
                charges.append(Command.create({
                    "product_id": product.id, "name": (row.get("note") or "").strip() or False, "qty": row["qty"],
                    "unit_price": row["cost"], "tax_id": row["tax_id"],
                }))
                continue
            vals_list.append({
                "company_id": company.id, "warehouse_id": wh.id, "date": day, "reason": "sale",
                "product_id": product.id, "qty_out": row["qty"], "unit_price": row["cost"], "tax_id": row["tax_id"],
                "purchase_ref": ref, "note": row.get("note") or False,
            })
        if not vals_list:
            raise UserError(_("Add at least one item kept in stock. A sale with no stock in it is an invoice: enter "
                              "it in Accounting."))
        if not partner:
            Partner = self.env["res.partner"]
            partner = Partner.search([("name", "=ilike", _escape_like(name)), ("parent_id", "=", False)], limit=1) \
                or Partner.sudo().create({"name": name, "company_id": False, "customer_rank": 1}).sudo(False)
            # Found by name rather than picked: a PIN it already has is not new.
            pin = self._sale_pin(values.get("partner_vat"), partner)
        Line._lock_purchase_supplier(partner)
        self._check_sale_ref(partner, ref)
        self._check_sale_cu(cu)
        self._sale_update_customer(partner, pin, terms)
        window = self.env["odin.stock.sale"].create({
            "company_id": company.id, "warehouse_id": wh.id, "partner_id": partner.id, "sale_ref": ref,
            "date": day, "pay_journal_id": journal.id or False, "charge_ids": charges, "cu_invoice_no": cu or False,
            "payment_term_id": (terms or partner.sudo().commercial_partner_id.with_company(company)
                                .property_payment_term_id).id or False,
        })
        for vals in vals_list:
            vals.update(partner_id=partner.id, partner_name=partner.name, stock_sale_id=window.id)
        lines = Line.with_context(odin_stock_book_posting=True).create(vals_list).with_context(
            odin_stock_book_posting=False)
        posted = bool(post and self._is_supervisor())
        if posted:
            lines.action_post()
        sale = window.sudo()
        return {
            "kind": "sale", "invoice": sale.invoice_id.name or "", "order": sale.order_id.name or "",
            "ids": lines.ids, "refs": lines.mapped("ref") if posted else [], "posted": posted,
            "paid_from": journal.name or "", "total": sale.order_id.amount_total, "purchase_ref": ref,
            "date": fields.Date.to_string(day),
            "due": fields.Date.to_string(sale.invoice_id.invoice_date_due) if sale.invoice_id else "",
        }
