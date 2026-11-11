from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class StockBookPurchase(models.Model):
    """One LPO or invoice entered in the purchase window: what its book lines
    do not hold. The supplier's costs that are not stock, how it was paid, and
    the documents it became."""

    _name = "odin.stock.purchase"
    _description = "Stock Book purchase"
    _order = "date desc, id desc"
    _rec_name = "purchase_ref"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, check_company=True)
    partner_id = fields.Many2one("res.partner", string="Supplier", required=True)
    purchase_ref = fields.Char(string="LPO / invoice no.", required=True)
    date = fields.Date(required=True)
    pay_journal_id = fields.Many2one("account.journal", string="Paid from", check_company=True,
                                     domain=[("type", "in", ("bank", "cash"))],
                                     help="Empty: not paid yet, the bill stays open.")
    line_ids = fields.One2many("odin.stock.line", "stock_purchase_id", string="Stock lines")
    charge_ids = fields.One2many("odin.stock.purchase.charge", "purchase_id", string="Other costs")
    order_id = fields.Many2one("purchase.order", readonly=True, copy=False)
    bill_id = fields.Many2one("account.move", readonly=True, copy=False)
    payment_id = fields.Many2one("account.payment", readonly=True, copy=False)
    undone_on = fields.Datetime(readonly=True, copy=False)
    undone_by_id = fields.Many2one("res.users", readonly=True, copy=False)

    def unlink(self):
        if self.filtered("order_id"):
            raise UserError(_("This purchase is posted. Reverse it in Purchases and Accounting."))
        return super().unlink()

    def _charge_order_lines(self, when):
        return [Command.create({
            "product_id": charge.product_id.id, "name": charge.name or charge.product_id.display_name,
            "product_qty": charge.qty, "product_uom_id": charge.product_id.uom_id.id,
            "price_unit": charge.unit_cost, "date_planned": when, "tax_ids": [Command.set(charge.tax_id.ids)],
        }) for charge in self.charge_ids]

    def _bill_and_pay(self, order):
        """The order's bill, posted on the day and under the number on the
        paper, and its payment. Odoo's own steps, as from the order's buttons.
        Anything that cannot be booked stops the whole purchase: never stock
        without its accounting."""
        self.ensure_one()
        order = order.sudo().with_company(self.company_id)
        # What is not stock arrived with the goods: billed in full under any
        # bill control policy.
        for line in order.order_line.filtered(lambda l: l.qty_received_method == "manual"):
            line.qty_received = line.product_qty
        order.action_create_invoice()
        bill = order.invoice_ids
        if len(bill) != 1:
            raise UserError(_("%(order)s did not make one bill. Nothing was saved.", order=order.display_name))
        bill.write({"invoice_date": self.date, "date": self.date, "ref": self.purchase_ref})
        if bill.currency_id.compare_amounts(bill.amount_total, order.amount_total):
            raise UserError(_("The bill for %(order)s comes to %(bill)s, the order to %(total)s. Nothing was saved.",
                              order=order.display_name, bill=bill.amount_total, total=order.amount_total))
        bill.action_post()
        self.write({"order_id": order.id, "bill_id": bill.id})
        if self.pay_journal_id:
            self.payment_id = self._pay(bill).id

    def _pay(self, bill):
        """Paid in full from the journal chosen, and checked to be in the ledger:
        a payment method with no outstanding account makes no journal entry."""
        journal = self.pay_journal_id.sudo()
        wizard = self.env["account.payment.register"].sudo().with_company(self.company_id).with_context(
            active_model="account.move", active_ids=bill.ids,
        ).create({"journal_id": journal.id, "payment_date": self.date})
        if not wizard.payment_method_line_id:
            raise UserError(_("%(journal)s has no outgoing payment method. Add one on the journal, or save this "
                              "purchase as not paid yet.", journal=journal.display_name))
        payment = wizard._create_payments()
        if not payment.move_id or bill.payment_state not in ("in_payment", "paid"):
            raise UserError(_("The payment from %(journal)s did not reach the accounts. Nothing was saved. Save this "
                              "purchase as not paid yet and pay the bill in Accounting.",
                              journal=journal.display_name))
        return payment


    def _undo(self):
        """The whole purchase taken back at once, in the order Odoo requires:
        the payment off the bill, the bill cancelled, the goods returned, the
        order cancelled. All of it or none. Each document stays, cancelled, and
        the goods going back are on the movement log."""
        self.ensure_one()
        if self.undone_on:
            raise UserError(_("%s is already undone.", self.purchase_ref))
        order = self.order_id.sudo()
        if not order:
            raise UserError(_("This purchase is not posted yet."))
        bill, payment = self.bill_id.sudo(), self.payment_id.sudo()
        if payment.is_matched:
            raise UserError(_("%(payment)s is matched with a bank statement line. Undo that match in Accounting first.",
                              payment=payment.display_name))
        Line = self.env["odin.stock.line"]
        for line in self.line_ids.filtered(lambda l: l.state == "posted"):
            onhand = Line._available(line.product_id, line.warehouse_id)[line.product_id.id]
            if onhand < line.qty_in:
                raise UserError(_("%(item)s has been used: %(onhand)s on hand, %(qty)s to return. Nothing was undone.",
                                  item=line.product_id.display_name, onhand=onhand, qty=line.qty_in))
        if payment:
            payment.action_draft()
            payment.action_cancel()
        if bill:
            if bill.payment_state not in ("not_paid", "invoicing_legacy"):
                raise UserError(_("%(bill)s has another payment on it. Remove it in Accounting first.",
                                  bill=bill.display_name))
            bill.button_draft()
            bill.button_cancel()
        for picking in order.picking_ids.filtered(
                lambda p: p.state == "done" and p.picking_type_id.code == "incoming"):
            self._return_picking(picking)
        if order.locked:
            order.button_unlock()
        order.button_cancel()
        self.write({"undone_on": fields.Datetime.now(), "undone_by_id": self.env.user.id})

    def _return_picking(self, picking):
        """Odoo's Return button, everything received, validated at once. Not
        in the book's posting context: the return goes on the log by itself."""
        wizard = self.env["stock.return.picking"].sudo().with_context(
            active_id=picking.id, active_ids=picking.ids, active_model="stock.picking",
        ).create({"picking_id": picking.id})
        for row in wizard.product_return_moves:
            row.quantity = row.move_id.quantity
        back = wizard._create_return()
        self.env["odin.stock.line"].browse()._validate_picking(back)


class StockBookPurchaseCharge(models.Model):
    """A cost on the LPO that is not stock: transport, labour. It goes on the
    purchase order and the bill, to the account of its product, not into stock."""

    _name = "odin.stock.purchase.charge"
    _description = "Stock Book purchase: other cost"
    _order = "id"

    purchase_id = fields.Many2one("odin.stock.purchase", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", required=True, domain=[("is_storable", "=", False)])
    name = fields.Char(string="Description")
    qty = fields.Float(digits="Product Unit", required=True)
    unit_cost = fields.Float(digits="Product Price", required=True)
    tax_id = fields.Many2one("account.tax", string="VAT")

    @api.constrains("qty", "unit_cost")
    def _check_positive(self):
        if self.filtered(lambda c: c.qty <= 0 or c.unit_cost <= 0):
            raise UserError(_("Other costs need a quantity and a cost."))
