"""Add bill: a purchase that is not stock, already paid. Printing paper,
electricity, a repair. Odoo's own route for it, in one step: a vendor bill
with lines coded to accounts, no product and no purchase order, posted, with
the scan of the paper, and its payment.

Vendor bills: https://www.odoo.com/documentation/19.0/applications/finance/accounting/vendor_bills.html
Payments: https://www.odoo.com/documentation/19.0/applications/finance/accounting/payments.html

Nothing here goes on the book: no stock moves, no book lines. The bill is
the record, in Accounting > Vendors > Bills.
"""

import base64
import binascii

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.mimetypes import guess_mimetype

from .stock_book import _escape_like
from .stock_sale import KRA_PIN

# What a bill line may be charged to: costs, and what is paid now and kept
# (prepaid insurance, a printer). Never receivable, payable, bank, tax or
# income, and never stock: see _bill_stock_accounts.
# Listed in this order where the vendor's own bills say nothing: costs first.
BILL_ACCOUNT_TYPES = ("expense", "expense_other", "expense_direct_cost",
                      "asset_prepayments", "asset_fixed", "asset_non_current")
# The scan of the paper: what a browser shows in the bill's preview.
BILL_FILE_TYPES = ("application/pdf", "image/jpeg", "image/png", "image/webp")
BILL_FILE_MAX_MB = 10
BILL_FILES_MAX = 5
ACCOUNT_LIMIT = 12


class AccountMove(models.Model):
    _inherit = "account.move"

    # The supplier's CU invoice number: KRA's pre-filled VAT return may list
    # the purchase by it rather than by the number printed on the paper. The
    # same field as x_ke_etims's, defined the same way, so that one column
    # holds it whichever of the two is installed and x_ke_etims_vat reads
    # what was typed here.
    ke_etims_supplier_invoice_ref = fields.Char(
        string="Supplier eTIMS Invoice No.", copy=False, tracking=True,
        help="The CU invoice number on the supplier's eTIMS invoice. Only eTIMS invoices KRA has received, with "
             "the company's PIN on them, count for input VAT and as expenses for income tax.")
    odin_supplier_cu_elsewhere = fields.Boolean(compute="_compute_odin_supplier_cu_elsewhere")

    def _compute_odin_supplier_cu_elsewhere(self):
        # x_ke_etims shows the field on the bill itself, beside its reverse
        # invoice box: shown once, not twice.
        elsewhere = "ke_etims_reverse_invoice" in self._fields
        for move in self:
            move.odin_supplier_cu_elsewhere = elsewhere


class StockBook(models.AbstractModel):
    _inherit = "odin.stock.book"

    # ------------------------------------------------------------ what a bill line can be
    @api.model
    def _bill_stock_accounts(self, company):
        """Where stock is valued and its closing posts. A bill charged there
        would put stock in the accounts that no count or move knows of."""
        categories = self.env["product.category"].sudo().with_company(company).search([])
        valuation = categories.property_stock_valuation_account_id | company.sudo().account_stock_valuation_id
        return (valuation | valuation.account_stock_variation_id | categories.property_price_difference_account_id
                | company.sudo().account_production_wip_account_id
                | company.sudo().account_production_wip_overhead_account_id)

    @api.model
    def _bill_accounts(self):
        company = self.env.company
        Account = self.env["account.account"].sudo().with_company(company)
        accounts = Account.search([*Account._check_company_domain(company),
                                   ("account_type", "in", BILL_ACCOUNT_TYPES)])
        return accounts - self._bill_stock_accounts(company)

    @api.model
    def _bill_account(self, account):
        return {"id": account.id, "code": account.code or "", "name": account.name,
                "label": f"{account.code} {account.name}" if account.code else account.name} if account else False

    @api.model
    def _bill_account_tax(self, account):
        """The VAT a line on this account starts with, as Odoo's own bill
        line takes it: the account's default purchase tax."""
        taxes = account.sudo().tax_ids.filtered(lambda t: t.type_tax_use == "purchase")
        return taxes._filter_taxes_by_company(self.env.company)[:1] & self._purchase_taxes()

    @api.model
    def _bill_usual_tax(self, partner):
        """The VAT last charged on this vendor's bills; none if never billed."""
        line = self.env["account.move.line"].sudo().search([
            ("company_id", "=", self.env.company.id), ("move_id.move_type", "=", "in_invoice"),
            ("parent_state", "=", "posted"), ("partner_id", "=", partner.commercial_partner_id.id),
            ("display_type", "=", "product"), ("tax_ids", "!=", False)], order="id desc", limit=1)
        return line.tax_ids[:1] & self._purchase_taxes()

    @api.model
    def _bill_vendor(self, partner):
        """The vendor as the window shows it: its KRA PIN, and what its lines
        usually are, so a regular bill takes only the amounts."""
        company = self.env.company
        commercial = partner.sudo().commercial_partner_id
        account_id = self.env["account.account"].sudo()._get_most_frequent_account_for_partner(
            company.id, commercial.id, "in_invoice")
        account = self._bill_accounts().filtered(lambda a: a.id == account_id)
        tax = self._bill_account_tax(account) or self._bill_usual_tax(partner)
        return {"id": partner.id, "name": partner.name, "vat": commercial.vat or "",
                "account": self._bill_account(account), "tax_id": tax.id or False}

    # ------------------------------------------------------------ what the window asks
    @api.model
    def _require_book_user(self):
        """Bills are made in sudo, and nothing of the book's own is written
        that its access rules would guard: the book's users only."""
        if not self.env.user.has_group("odin_stock_book.group_stock_book_user"):
            raise AccessError(_("Only Stock Book users can enter bills here."))

    @api.model
    def book_bill_options(self):
        self._require_book_user()
        options = self.book_purchase_options()
        options.update(can_post=True, max_file_mb=BILL_FILE_MAX_MB, max_files=BILL_FILES_MAX,
                       file_types=list(BILL_FILE_TYPES))
        return options

    @api.model
    def book_bill_suppliers(self, text):
        self._require_book_user()
        found = self.book_purchase_suppliers(text)
        Partner = self.env["res.partner"]
        return {"items": [{**item, "vat": Partner.browse(item["id"]).commercial_partner_id.vat or ""}
                          for item in found["items"]], "can_create": found["can_create"]}

    @api.model
    def book_bill_vendor(self, partner_id):
        """What a picked vendor's new lines start with."""
        self._require_book_user()
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        return self._bill_vendor(partner) if partner else False

    @api.model
    def book_bill_accounts(self, text, partner_id=False):
        """Accounts for a bill line, by any words of the code or name: those
        this vendor's bills use first, most used first, then by code."""
        self._require_book_user()
        accounts = self._bill_accounts()
        for word in (text or "").split():
            word = word.casefold()
            accounts = accounts.filtered(lambda a, w=word: (a.code or "").casefold().startswith(w)
                                         or w in (a.name or "").casefold())
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        rank = {}
        if partner:
            # Only those its bills used, as Odoo's own most-frequent account.
            used = self.env["account.account"].sudo()._get_most_frequent_accounts_for_partner(
                self.env.company.id, partner.commercial_partner_id.id, "in_invoice", filter_never_user_accounts=True)
            rank = {account_id: index for index, account_id in enumerate(used)}
        accounts = accounts.sorted(lambda a: (rank.get(a.id, len(rank)), BILL_ACCOUNT_TYPES.index(a.account_type),
                                              a.code or "", a.name))
        return [{**self._bill_account(a), "tax_id": self._bill_account_tax(a).id or False,
                 "used": a.id in rank} for a in accounts[:ACCOUNT_LIMIT]]

    @api.model
    def book_bill_check(self, partner_id, ref, ignore_ids=None):
        self._require_book_user()
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        try:
            self.env["odin.stock.line"]._check_purchase_ref(partner, ref, self.env.company)
        except UserError as error:
            return str(error)
        return False

    @api.model
    def book_bill_cu_check(self, cu):
        self._require_book_user()
        try:
            self._bill_check_cu(cu)
        except UserError as error:
            return str(error)
        return False

    @api.model
    def book_bill_totals(self, rows, partner_id=False):
        """Each line's amount before VAT, its VAT and its total, and the
        bill's VAT by rate: what the bill will show, by Odoo's own tax
        computation, to compare with the paper."""
        self._require_book_user()
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        company = self.env.company
        base_lines = self._purchase_tax_lines(rows, partner)
        summary = self.env["account.tax"]._get_tax_totals_summary(
            base_lines=base_lines, currency=company.currency_id, company=company)
        lines = []
        for line in base_lines:
            details = line["tax_details"]
            lines.append({"subtotal": details["total_excluded_currency"],
                          "vat": details["total_included_currency"] - details["total_excluded_currency"],
                          "total": details["total_included_currency"]})
        return {
            "lines": lines, "subtotal": summary["base_amount_currency"], "vat": summary["tax_amount_currency"],
            "total": summary["total_amount_currency"],
            "taxes": [{"name": group["group_name"], "amount": group["tax_amount_currency"]}
                      for subtotal in summary["subtotals"] for group in subtotal["tax_groups"]],
        }

    # ------------------------------------------------------------ checks
    @api.model
    def _bill_pin(self, text, partner=None):
        """The vendor's KRA PIN as KRA writes it. One the vendor already has
        stays as it is, a foreign tax ID say; a new one must be a KRA PIN."""
        pin = "".join((text or "").split()).upper()
        known = "".join((partner.sudo().commercial_partner_id.vat or "").split()).upper() if partner else ""
        if pin and pin != known and not KRA_PIN.fullmatch(pin):
            raise UserError(_("%s is not a KRA PIN: A or P, nine digits and a letter, e.g. P052112729P. A supplier "
                              "outside Kenya keeps its tax ID in Accounting > Vendors.", text.strip()))
        return pin

    @api.model
    def _bill_check_cu(self, cu):
        """A CU invoice number is one invoice: entered on one bill."""
        cu = " ".join((cu or "").split())
        if not cu:
            return
        bill = self.env["account.move"].sudo().search([
            ("company_id", "=", self.env.company.id), ("move_type", "in", ("in_invoice", "in_refund")),
            ("state", "!=", "cancel"), ("ke_etims_supplier_invoice_ref", "=ilike", _escape_like(cu))], limit=1)
        if bill:
            raise UserError(_("CU invoice no. %(cu)s is already on %(bill)s from %(supplier)s.",
                              cu=cu, bill=bill.name, supplier=bill.partner_id.display_name))

    @api.model
    def _bill_files(self, files):
        """The scans sent with the bill, read as what they are, not as what
        the browser called them."""
        files = files or []
        if len(files) > BILL_FILES_MAX:
            raise UserError(_("Attach at most %s files to one bill.", BILL_FILES_MAX))
        result = []
        for file in files:
            name = " ".join((file.get("name") or "").split())[:200] or _("Bill")
            try:
                raw = base64.b64decode(file.get("data") or "", validate=True)
            except (binascii.Error, ValueError):
                raw = b""
            if not raw:
                raise UserError(_("%s could not be read. Attach it again.", name))
            if len(raw) > BILL_FILE_MAX_MB * 1024 * 1024:
                raise UserError(_("%(name)s is larger than %(mb)s MB. Scan it again at a lower resolution.",
                                  name=name, mb=BILL_FILE_MAX_MB))
            mimetype = guess_mimetype(raw)
            if mimetype not in BILL_FILE_TYPES:
                raise UserError(_("%s is not a PDF or a photo (JPG, PNG). Attach the scan of the bill.", name))
            result.append({"name": name, "raw": raw, "mimetype": mimetype})
        return result

    @api.model
    def _bill_pay(self, bill, journal, day):
        """Paid in full from the journal chosen, as the bill's Pay button
        does, and checked to be in the ledger."""
        wizard = self.env["account.payment.register"].sudo().with_company(bill.company_id).with_context(
            active_model="account.move", active_ids=bill.ids,
        ).create({"journal_id": journal.id, "payment_date": day})
        if not wizard.payment_method_line_id:
            raise UserError(_("%(journal)s has no outgoing payment method. Add one on the journal, or choose "
                              "another.", journal=journal.display_name))
        payment = wizard._create_payments()
        if not payment.move_id or bill.payment_state not in ("in_payment", "paid"):
            raise UserError(_("The payment from %(journal)s did not reach the accounts. Nothing was saved.",
                              journal=journal.display_name))
        return payment

    # ------------------------------------------------------------ save
    @api.model
    def book_bill_save(self, values):
        """One bill from the Add bill window: posted, with its scan, and paid.
        All of it or none. Anyone who uses the book can enter one; the bill
        and payment are made for them, recorded as theirs."""
        self._require_book_user()
        company = self.env.company
        Line = self.env["odin.stock.line"]
        ref = " ".join((values.get("purchase_ref") or "").split())
        if not ref:
            raise UserError(_("Type the bill or receipt number."))
        partner = self.env["res.partner"].browse(values.get("partner_id") or []).exists()
        name = " ".join((values.get("partner_name") or "").split())
        if not partner and not name:
            raise UserError(_("Pick the supplier."))
        day = fields.Date.to_date(values.get("date"))
        if not day:
            raise UserError(_("Type the date on the bill."))
        if day > fields.Date.context_today(self):
            raise UserError(_("The bill date cannot be in the future: %s.", day.strftime("%d %b %Y")))
        journal = self.env["account.journal"].sudo().browse(values.get("pay_journal_id") or []).exists()
        if not journal or journal not in self._purchase_pay_journals():
            raise UserError(_("Choose where it was paid from."))
        rows = values.get("lines") or []
        if not rows:
            raise UserError(_("Add at least one line: what the bill is for."))
        taxes, accounts = self._purchase_taxes(), self._bill_accounts()
        invoice_lines = []
        for number, row in enumerate(rows, 1):
            what = " ".join((row.get("name") or "").split())
            if not what:
                raise UserError(_("Line %s: say what it was for.", number))
            account = accounts.filtered(lambda a: a.id == row.get("account_id"))
            if not account:
                raise UserError(_("%s: choose the account from the list.", what))
            qty = row.get("qty")
            if qty is None or qty == "":
                qty = 1.0
            if qty <= 0:
                raise UserError(_("%s: type the quantity.", what))
            if (row.get("cost") or 0) <= 0:
                raise UserError(_("%s: type the amount.", what))
            tax = taxes.filtered(lambda t: t.id == row.get("tax_id"))
            if not tax:
                raise UserError(_("%s: choose the VAT, or the rate with none.", what))
            invoice_lines.append(Command.create({
                "name": what, "account_id": account.id, "quantity": qty, "price_unit": row["cost"],
                # Always stated: left out, Odoo takes the account's default tax.
                "tax_ids": [Command.set(tax.ids)],
            }))
        pin = self._bill_pin(values.get("partner_vat"), partner)
        cu = " ".join((values.get("cu_invoice_no") or "").split())
        files = self._bill_files(values.get("files"))
        if not partner:
            # Asked for by name in the window: as typed. Sudo as the book does
            # when posting: storekeepers need not create contacts in general.
            Partner = self.env["res.partner"]
            partner = Partner.search([("name", "=ilike", _escape_like(name)), ("parent_id", "=", False)], limit=1) \
                or Partner.sudo().create({"name": name, "company_id": False, "supplier_rank": 1}).sudo(False)
            # Found by name rather than picked: a PIN it already has is not new.
            pin = self._bill_pin(values.get("partner_vat"), partner)
        Line._lock_purchase_supplier(partner)
        Line._check_purchase_ref(partner, ref, company)
        self._bill_check_cu(cu)
        commercial = partner.sudo().commercial_partner_id
        if pin and pin != "".join((commercial.vat or "").split()).upper():
            commercial.vat = pin
        # Sudo: the bill and payment are made for whoever uses the book, who
        # need not be an accountant. The user stays the one on the bill and
        # in its chatter.
        bill = self.env["account.move"].sudo().with_company(company).create({
            "move_type": "in_invoice", "company_id": company.id, "partner_id": partner.id,
            "currency_id": company.currency_id.id, "ref": ref, "invoice_date": day,
            "ke_etims_supplier_invoice_ref": cu or False, "invoice_line_ids": invoice_lines,
        })
        # In the accounts on the day of the paper, as the book's purchases
        # are. Odoo's own default for a past bill is today, or its month's
        # last day; it stands only where a lock date closes the bill's day.
        if not bill._get_violated_lock_dates(day, bool(bill.invoice_line_ids.tax_ids)):
            bill.date = day
        bill.action_post()
        attachments = self.env["ir.attachment"].sudo().create([{
            **file, "res_model": "account.move", "res_id": bill.id} for file in files])
        payment = self._bill_pay(bill, journal, day)
        # Posted from code, not through the upload route: Odoo then reads
        # nothing from the scan into the bill. Its first PDF or photo becomes
        # the bill's main attachment, shown beside it.
        bill.message_post(
            body=_("Entered in Stock Book and paid from %(journal)s.", journal=journal.display_name),
            attachment_ids=attachments.ids, subtype_xmlid="mail.mt_note")
        return {
            "kind": "bill", "bill": bill.name, "bill_id": bill.id, "total": bill.amount_total,
            "paid_from": journal.display_name, "payment": payment.name, "payment_state": bill.payment_state,
            "purchase_ref": ref, "date": fields.Date.to_string(bill.invoice_date), "files": len(attachments),
            "can_open": self.env.user.has_group("account.group_account_invoice")
                        or self.env.user.has_group("account.group_account_readonly"),
        }
