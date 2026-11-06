import logging
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ADJUST_CODE = "500106"
PETTY_CODES = ("500110", "500120")


class StockBook(models.AbstractModel):
    """Bringing past stock into the accounts, by Odoo's own steps. Each of
    these only reports until it is called with ``dry_run=False``."""

    _inherit = "odin.stock.book"

    @api.model
    def _require_accountant(self):
        if not self.env.is_superuser() and not self.env.user.has_group("account.group_account_manager"):
            raise UserError(_("Only an accounting administrator can do this."))

    # ------------------------------------------------------------ orders with no receipt and no bill
    @api.model
    def _imported_orders(self):
        """Confirmed orders whose goods reached stock another way: every
        receipt cancelled, nothing received, nothing billed."""
        orders = self.env["purchase.order"].sudo().search(
            [("company_id", "=", self.env.company.id), ("state", "=", "purchase"), ("invoice_ids", "=", False)],
            order="date_order, id")
        return orders.filtered(lambda o: not o.picking_ids.filtered(lambda p: p.state != "cancel")
                               and not any(o.order_line.mapped("qty_received")))

    @api.model
    def _petty_matches(self, orders, codes=PETTY_CODES):
        """Petty cash lines that look like the same purchase: the same amount
        within three days, on a materials account. Already in the expenses,
        so a bill alone would count it twice."""
        if "odin.petty.cash.line" not in self.env:
            return []
        Petty = self.env["odin.petty.cash.line"].sudo()
        lines = Petty.search([("state", "=", "posted"), ("account_id.code", "in", list(codes))], order="date, id")
        used, pairs = set(), []
        for order in orders:
            day = fields.Date.context_today(self, order.date_order)
            for line in lines:
                if line.id not in used and abs(line.amount - order.amount_total) < 0.005 \
                        and abs((line.date - day).days) <= 3:
                    used.add(line.id)
                    pairs.append({"order_id": order.id, "order": order.name, "supplier": order.partner_id.name,
                                  "date": fields.Date.to_string(day), "amount": order.amount_total,
                                  "petty_id": line.id, "petty_date": fields.Date.to_string(line.date),
                                  "description": line.description, "account": line.account_id.display_name})
                    break
        return pairs

    @api.model
    def book_bill_imported(self, dry_run=True, order_ids=None, include_partial=False, petty_pairs=None,
                           pay_journal_id=False):
        """A dated bill for each order whose receipt was cancelled so stock is
        not added twice. No stock moves. ``petty_pairs``: [order id, petty line
        id] pairs, from the report, that are paid by that petty cash line."""
        self._require_accountant()
        orders = self._imported_orders()
        if order_ids:
            orders = orders.filtered(lambda o: o.id in order_ids)
        partial = orders.filtered(lambda o: "PARTIAL" in (o.origin or ""))
        todo = orders if include_partial else orders - partial
        report = {
            "orders": len(orders), "untaxed": sum(orders.mapped("amount_untaxed")),
            "vat": sum(orders.mapped("amount_tax")), "total": sum(orders.mapped("amount_total")),
            "partial": [{"order": o.name, "ref": o.partner_ref, "total": o.amount_total, "origin": o.origin}
                        for o in partial],
            "partial_total": sum(partial.mapped("amount_total")),
            "to_bill": len(todo), "to_bill_total": sum(todo.mapped("amount_total")),
            "petty": self._petty_matches(orders),
        }
        if dry_run:
            return report
        journal = self.env["account.journal"].sudo().browse(pay_journal_id or []).exists()
        if pay_journal_id and journal not in self._purchase_pay_journals():
            raise UserError(_("Payments from that journal would not reach the ledger."))
        paid_by_petty = {order: petty for order, petty in (petty_pairs or [])}
        billed, failed = [], []
        for order in todo:
            try:
                with self.env.cr.savepoint():
                    bill = self._bill_imported_order(order)
                    if order.id in paid_by_petty:
                        self._clear_with_petty(bill, paid_by_petty[order.id])
                    elif journal:
                        self._pay_imported(bill, journal)
                billed.append({"order": order.name, "bill": bill.name, "total": bill.amount_total})
            except UserError as error:
                self.env.invalidate_all()
                failed.append({"order": order.name, "why": error.args[0]})
        report.update(billed=billed, failed=failed)
        return report

    @api.model
    def _bill_imported_order(self, order):
        """Odoo's own Create Bill, with the received quantity stated by hand on
        each line: the goods arrived, and the line takes it from there rather
        than from stock moves. That is what stops a second receipt."""
        order = order.sudo().with_company(order.company_id)
        for line in order.order_line.filtered(lambda l: not l.display_type):
            line.write({"qty_received_method": "manual", "qty_received_manual": line.product_qty})
        order.action_create_invoice()
        bill = order.invoice_ids
        if len(bill) != 1:
            raise UserError(_("%(order)s did not make one bill.", order=order.display_name))
        day = fields.Date.context_today(self, order.date_order)
        bill.write({"invoice_date": day, "date": day, "ref": order.partner_ref or order.name})
        if bill.currency_id.compare_amounts(bill.amount_total, order.amount_total):
            raise UserError(_("The bill for %(order)s comes to %(bill)s, the order to %(total)s.",
                              order=order.display_name, bill=bill.amount_total, total=order.amount_total))
        bill.action_post()
        return bill

    @api.model
    def _clear_with_petty(self, bill, petty_id):
        """A bill already paid from petty cash, where it went to an expense:
        that expense moves to the supplier's account, and clears the bill."""
        petty = self.env["odin.petty.cash.line"].sudo().browse(petty_id).exists()
        if not petty or petty.state != "posted":
            raise UserError(_("Petty cash line %s is not posted.", petty_id))
        if bill.currency_id.compare_amounts(petty.amount, bill.amount_total):
            raise UserError(_("Petty cash line %(line)s is not for the amount of %(bill)s.",
                              line=petty_id, bill=bill.name))
        payable = bill.line_ids.filtered(lambda l: l.account_id.account_type == "liability_payable")
        journal = self.env["account.journal"].sudo().search(
            [("type", "=", "general"), ("code", "=", "MISC"), ("company_id", "=", bill.company_id.id)], limit=1) \
            or self.env["account.journal"].sudo().search(
                [("type", "=", "general"), ("company_id", "=", bill.company_id.id)], limit=1)
        name = _("%(bill)s paid from petty cash: %(what)s", bill=bill.name, what=petty.description)
        entry = self.env["account.move"].sudo().create({
            "move_type": "entry", "journal_id": journal.id, "date": petty.date, "ref": name,
            "line_ids": [
                Command.create({"account_id": payable[:1].account_id.id, "partner_id": bill.partner_id.id,
                                "name": name, "debit": petty.amount, "credit": 0.0}),
                Command.create({"account_id": petty.account_id.id, "name": name,
                                "debit": 0.0, "credit": petty.amount}),
            ],
        })
        entry.action_post()
        (entry.line_ids.filtered(lambda l: l.account_id == payable[:1].account_id) | payable).reconcile()
        if bill.payment_state not in ("paid", "in_payment"):
            raise UserError(_("%(bill)s did not clear with petty cash line %(line)s.", bill=bill.name, line=petty_id))
        return entry

    @api.model
    def _pay_imported(self, bill, journal):
        wizard = self.env["account.payment.register"].sudo().with_company(bill.company_id).with_context(
            active_model="account.move", active_ids=bill.ids,
        ).create({"journal_id": journal.id, "payment_date": bill.invoice_date})
        payment = wizard._create_payments()
        if not payment.move_id or bill.payment_state not in ("in_payment", "paid"):
            raise UserError(_("The payment of %(bill)s did not reach the accounts.", bill=bill.name))
        return payment

    # ------------------------------------------------------------ stock in the ledger
    @api.model
    def _inventory_location(self):
        return self.env["stock.location"].sudo().search(
            [("usage", "=", "inventory"), ("company_id", "=", self.env.company.id)], limit=1)

    @api.model
    def book_ledger_setup(self, dry_run=True):
        """What makes stock leaving reach the ledger from now on. Scrap and
        adjustments go through the Inventory adjustment location: an account
        on it makes Odoo post each one (Odoo's own setting). Deliveries post at
        the customer invoice. Whatever else moved, the month-end closing brings
        the Stock account to the stock's value. Sales must carry an invoice."""
        self._require_accountant()
        company = self.env.company
        Account = self.env["account.account"].sudo()
        location = self._inventory_location()
        account = Account.search([("code", "=", ADJUST_CODE), ("company_ids", "in", company.id)], limit=1)
        cron = self.env.ref("odin_stock_book.ir_cron_stock_book_close").sudo()
        report = {
            "location": location.complete_name, "location_account": location.valuation_account_id.display_name or "",
            "account": account.display_name or _("%s Stock Losses & Adjustments (to create)", ADJUST_CODE),
            "stock_account": company.account_stock_valuation_id.display_name or "",
            "journal": company.account_stock_journal_id.display_name or "",
            "cron_active": cron.active, "sales_need_invoice": company.odin_sale_needs_invoice,
        }
        if dry_run:
            return report
        if not company.account_stock_valuation_id or not company.account_stock_journal_id:
            raise UserError(_("Set the Stock Valuation Account and the Stock Journal in the Inventory settings first."))
        if not account:
            account = Account.create({"code": ADJUST_CODE, "name": "Stock Losses & Adjustments",
                                      "account_type": "expense_direct_cost", "company_ids": [Command.set(company.ids)]})
        location.valuation_account_id = account
        cron.active = True
        company.sudo().odin_sale_needs_invoice = True
        report.update(location_account=account.display_name, cron_active=True, sales_need_invoice=True)
        return report

    @api.model
    def book_stock_closing(self, at_date=False, dry_run=True):
        """Odoo's Stock Closing, as the Inventory Valuation report's button
        makes it: what it would post, then posted when asked."""
        self._require_accountant()
        company = self.env.company.sudo()
        day = fields.Date.to_date(at_date) if at_date else None
        lines = company._action_close_stock_valuation(at_date=day)
        Account = self.env["account.account"].sudo()
        report = {
            "date": fields.Date.to_string(day or fields.Date.context_today(self)),
            "stock_value": sum(company.stock_value(at_date=day).values()),
            "ledger_value": sum(company.stock_accounting_value(at_date=day).values()),
            "lines": [{"account": Account.browse(v["account_id"]).display_name, "name": v["name"],
                       "debit": v["debit"], "credit": v["credit"]} for v in lines],
        }
        if dry_run or not lines:
            return report
        action = company.action_close_stock_valuation(at_date=day, auto_post=True)
        report["move_id"] = action["res_id"]
        return report

    @api.model
    def _cron_close_stock(self):
        """Each month closed on the first of the next. Odoo's own monthly
        schedule does not run: _cron_post_stock_valuation asks for period
        daily and monthly at once at the end of a month, which nothing is."""
        today = fields.Date.context_today(self)
        month_end = today.replace(day=1) - timedelta(days=1)
        companies = self.env["res.company"].sudo().search(
            [("odin_sale_needs_invoice", "=", True), ("account_stock_journal_id", "!=", False),
             ("account_stock_valuation_id", "!=", False)])
        for company in companies:
            try:
                with self.env.cr.savepoint():
                    if company._action_close_stock_valuation(at_date=month_end):
                        company.action_close_stock_valuation(at_date=month_end, auto_post=True)
            except UserError as error:
                self.env.invalidate_all()
                _logger.warning("Stock Book closing of %s for %s: %s", company.name, month_end, error)

    # ------------------------------------------------------------ what happened outside the book
    @api.model
    def book_log_missing(self, dry_run=True, date_from=False):
        """Stock changes made directly in Inventory, Manufacturing, Purchases or
        Sales that are not on the movement log: reported, and logged when asked.
        ``date_from``: leave out what came before, such as an opening count."""
        if not self._is_supervisor():
            raise UserError(_("Only a Stock Book supervisor can do this."))
        domain = [("state", "=", "done"), ("company_id", "=", self.env.company.id), ("product_id.is_storable", "=", True)]
        if date_from:
            domain.append(("date", ">=", fields.Datetime.to_datetime(date_from)))
        Move = self.env["stock.move"].sudo()
        moves = Move.search(domain, order="date, id")
        # Lines removed from the book with their reversal still log their moves.
        logged = set(self.env["odin.stock.line"].sudo().with_context(active_test=False).search(
            [("move_ids", "!=", False)]).move_ids.ids)
        missing = moves.filtered(lambda m: m.id not in logged)._stock_book_crossing()
        missing = missing.filtered(lambda m: not m.raw_material_production_id)
        kinds = {}
        for move in missing:
            kind = move._stock_book_values()["reason"]
            kinds[kind] = kinds.get(kind, 0) + 1
        report = {"moves": len(missing), "by_reason": kinds,
                  "first": fields.Datetime.to_string(missing[:1].date) if missing else False,
                  "last": fields.Datetime.to_string(missing[-1:].date) if missing else False}
        if not dry_run:
            missing._log_in_stock_book()
        return report


class ResCompany(models.Model):
    _inherit = "res.company"

    odin_sale_needs_invoice = fields.Boolean(
        string="Stock Book sales need an invoice",
        help="A sale on the Stock Book posts with its sales order, delivery and invoice, and cannot post without them.")


