import re
from collections import Counter

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero

from .mpesa_statement import send_money_charge

STATUS = [
    ("need_amount", "Add the amount"),
    ("need_description", "Add what it was for"),
    ("need_category", "Pick a category"),
    ("need_stock", "Add product and quantity"),
    ("need_receipt", "Attach the receipt"),
    ("ready", "Ready to approve"),
    ("posted", "Approved"),
    ("cancelled", "Cancelled"),
]
STATUS_TONE = {"need_amount": "warning", "need_description": "warning", "need_category": "warning",
               "need_stock": "info", "need_receipt": "warning", "ready": "primary",
               "posted": "success", "cancelled": "muted"}

# Set by numbering, approving, resetting, the statement and the receipts,
# never by hand or over the API: that would mark a voucher approved without
# its entry, or put an approved one back to draft with its entry still posted.
SYSTEM_FIELDS = {"state", "voucher", "voucher_seq", "move_id", "approved_by_id", "approved_on", "picking_id",
                 "source", "statement_balance", "charge_from_statement", "receipt_count"}
# What an approved voucher keeps as its entry has it.
LOCKED_FIELDS = {"amount", "charge", "account_id", "date", "kind", "float_id", "product_id", "quantity",
                 "description", "paid_to", "rule_id", "analytic_distribution", "method", "partner_id"}
SYSTEM_CONTEXT = "odin_petty_cash_system"


def escape_like(value):
    """A name searched with =ilike: % and _ in it are letters, not wildcards."""
    return (value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class PettyCashLine(models.Model):
    """One voucher: a row of the petty cash book."""

    _name = "odin.petty.cash.line"
    _description = "Petty cash voucher"
    _inherit = ["mail.thread", "analytic.mixin"]
    _order = "date desc, voucher_seq desc, id desc"
    _rec_name = "voucher"

    float_id = fields.Many2one("odin.petty.cash.float", required=True, index=True, ondelete="restrict",
                               default=lambda self: self.env["odin.petty.cash.float"].search([], limit=1))
    company_id = fields.Many2one(related="float_id.company_id", store=True)
    currency_id = fields.Many2one(related="float_id.currency_id")
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    voucher = fields.Char(readonly=True, copy=False, index=True)
    voucher_seq = fields.Integer(readonly=True, copy=False)
    kind = fields.Selection([("payment", "Payment"), ("topup", "Float top-up")], default="payment", required=True)
    description = fields.Char(tracking=True)
    paid_to = fields.Char(tracking=True)
    partner_id = fields.Many2one("res.partner", string="Contact", check_company=True)
    phone = fields.Char()
    method = fields.Selection([("mpesa", "M-Pesa"), ("cash", "Cash")], default="mpesa", required=True)
    mpesa_code = fields.Char(string="M-Pesa code", copy=False, tracking=True)
    amount = fields.Monetary(string="Paid", tracking=True)
    charge = fields.Monetary(string="Charges", compute="_compute_charge", store=True, readonly=False,
                             precompute=True)
    charge_from_statement = fields.Boolean(readonly=True, copy=False)
    charge_manual = fields.Boolean(
        string="Charge typed in", copy=False,
        help="Typed in the book: the tariff no longer fills it in when the amount, phone or method changes. "
             "Clear the charge in the book to use the tariff again.")
    total = fields.Monetary(compute="_compute_total", store=True)
    signed_total = fields.Monetary(compute="_compute_total", store=True,
                                   help="Effect on the float: top-ups add, payments take away.")
    rule_id = fields.Many2one("account.reconcile.model", string="Category", compute="_compute_rule",
                              store=True, readonly=False, precompute=True, check_company=True)
    account_id = fields.Many2one("account.account", compute="_compute_account", store=True, readonly=False,
                                 precompute=True, check_company=True)
    analytic_distribution = fields.Json(compute="_compute_account", store=True, readonly=False, precompute=True)
    needs_stock = fields.Boolean(compute="_compute_needs_stock", store=True)
    product_id = fields.Many2one("product.product", domain=[("is_storable", "=", True)], check_company=True)
    quantity = fields.Float(digits="Product Unit")
    product_uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    picking_id = fields.Many2one("stock.picking", readonly=True, copy=False)
    statement_balance = fields.Monetary(readonly=True, copy=False,
                                        help="Float balance after this payment, as printed on the M-Pesa statement.")
    source = fields.Selection([("manual", "Typed"), ("statement", "From statement"),
                               ("matched", "Typed, confirmed by statement")],
                              default="manual", readonly=True, copy=False)
    receipt_count = fields.Integer(readonly=True, copy=False,
                                   help="Receipts attached (photos or PDFs). Kept up to date by the attachments.")
    state = fields.Selection([("draft", "Draft"), ("posted", "Approved"), ("cancelled", "Cancelled")],
                             default="draft", required=True, readonly=True, copy=False, tracking=True)
    status_key = fields.Selection(STATUS, string="Status", compute="_compute_status", store=True)
    move_id = fields.Many2one("account.move", readonly=True, copy=False)
    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)

    _code_unique = models.Constraint(
        "unique(float_id, mpesa_code)",
        "This M-Pesa code is already in the book. Each M-Pesa payment can only be recorded once.")
    _voucher_unique = models.UniqueIndex(
        "(float_id, voucher) WHERE voucher IS NOT NULL", "Another voucher of this float has this number.")

    # ------------------------------------------------------------ computes
    @api.depends("amount", "method", "phone", "kind", "charge_manual")
    def _compute_charge(self):
        for line in self:
            # An approved voucher keeps the charge its entry has (the statement
            # adding a phone number later must not change it), and a charge
            # someone typed stays as they typed it.
            if line.charge_from_statement or line.state == "posted" or line.charge_manual:
                continue
            charge = line._tariff_charge()
            if charge is not None:
                line.charge = charge

    def _tariff_charge(self):
        """The charge the book fills in. Sending to a phone has a known tariff;
        cash and top-ups have none; pay bill and till charges vary and arrive
        with the statement (None: not known yet)."""
        self.ensure_one()
        phone = re.sub(r"\D", "", self.phone or "")
        if self.kind == "payment" and self.method == "mpesa" and len(phone) >= 9 and self.amount:
            return send_money_charge(self.amount)
        if self.method == "cash" or self.kind == "topup":
            return 0.0
        return None

    @api.depends("amount", "charge", "kind")
    def _compute_total(self):
        for line in self:
            line.total = line.amount + (line.charge if line.kind == "payment" else 0.0)
            line.signed_total = line.amount if line.kind == "topup" else -line.total

    @api.depends("description", "paid_to", "kind", "float_id")
    def _compute_rule(self):
        for line in self:
            if line.state == "posted":
                continue
            line.rule_id = line._match_rule()

    @api.depends("rule_id", "kind")
    def _compute_account(self):
        for line in self:
            if line.state == "posted":
                continue
            rule_line = line.rule_id.line_ids[:1]
            account = rule_line.account_id
            if not account and line.kind == "topup":
                account = line.float_id.topup_account_id
            line.account_id = account
            line.analytic_distribution = rule_line.analytic_distribution or False

    @api.depends("rule_id", "float_id.stock_rule_ids")
    def _compute_needs_stock(self):
        for line in self:
            line.needs_stock = bool(line.rule_id and line.rule_id in line.float_id.stock_rule_ids)

    @api.depends("state", "amount", "description", "account_id", "needs_stock", "product_id", "quantity",
                 "method", "mpesa_code", "receipt_count", "kind")
    def _compute_status(self):
        for line in self:
            if line.state != "draft":
                line.status_key = line.state
            elif float_is_zero(line.amount, precision_rounding=line.currency_id.rounding or 0.01):
                line.status_key = "need_amount"
            elif not (line.description or "").strip():
                line.status_key = "need_description"
            elif not line.account_id:
                line.status_key = "need_category"
            elif line.needs_stock and (not line.product_id or line.quantity <= 0):
                line.status_key = "need_stock"
            elif line.method == "cash" and line.kind == "payment" and not line.receipt_count:
                line.status_key = "need_receipt"
            else:
                line.status_key = "ready"

    def _refresh_receipt_count(self):
        groups = dict(self.env["ir.attachment"].sudo()._read_group(
            [("res_model", "=", self._name), ("res_id", "in", self.ids)], ["res_id"], ["__count"]))
        for line in self.with_context(**{SYSTEM_CONTEXT: True}):
            line.receipt_count = groups.get(line.id, 0)

    # ------------------------------------------------------------ rules
    def _match_rule(self):
        """First of the float's matching rules whose label condition fits the
        description, in the rules' own order (Accounting > Reconciliation Models)."""
        self.ensure_one()
        text = (self.description or "").strip()
        if not text or not self.float_id:
            return self.env["account.reconcile.model"]
        if self.kind == "topup":
            text = f"FLOAT TOP UP {text}"
        rules = self.env["account.reconcile.model"].search(self.float_id._rule_domain(), order="sequence, id")
        # The description first, as the rules were written for it; then with
        # the payee, so "SHOPPING" paid to "NAIVAS" still finds its rule.
        for candidate in filter(None, [text, f"{text} {self.paid_to}" if self.paid_to else None]):
            rule = self._first_rule(rules, candidate)
            if rule:
                return rule
        return self.env["account.reconcile.model"]

    @api.model
    def _first_rule(self, rules, text):
        for rule in rules:
            param = rule.match_label_param or ""
            if rule.match_label == "contains" and param.lower() in text.lower():
                return rule
            if rule.match_label == "not_contains" and param.lower() not in text.lower():
                return rule
            if rule.match_label == "match_regex":
                try:
                    if re.search(param, text, re.IGNORECASE):
                        return rule
                except re.error:
                    continue
        return self.env["account.reconcile.model"]

    # ------------------------------------------------------------ create/write
    def _check_system_fields(self, vals, creating=False):
        if self.env.context.get(SYSTEM_CONTEXT):
            return
        touched = {key for key in SYSTEM_FIELDS & vals.keys()
                   if not (creating and (key, vals[key]) in (("state", "draft"), ("source", "manual")))}
        if touched:
            raise UserError(_("Numbers, approvals and what the statement says are set by Petty Cash itself: "
                              "approve, reset or cancel the voucher instead of changing %s.",
                              ", ".join(sorted(touched))))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_system_fields(vals, creating=True)
            for key in ("description", "paid_to", "mpesa_code"):
                if vals.get(key):
                    vals[key] = " ".join(str(vals[key]).split()).upper()
        lines = super().create(vals_list)
        for line in lines:
            line._assign_voucher()
        return lines

    def write(self, vals):
        self._check_system_fields(vals)
        for key in ("description", "paid_to", "mpesa_code"):
            if vals.get(key):
                vals[key] = " ".join(str(vals[key]).split()).upper()
        posted = self.filtered(lambda l: l.state == "posted")
        if posted and not self.env.context.get(SYSTEM_CONTEXT):
            if LOCKED_FIELDS & vals.keys():
                raise UserError(_("An approved voucher cannot be changed. Reset it to draft first."))
            # The statement may give an approved voucher the code it lacked;
            # a code it already has stays, as its entry has it.
            if "mpesa_code" in vals and posted.filtered(lambda l: l.mpesa_code and l.mpesa_code != vals["mpesa_code"]):
                raise UserError(_("An approved voucher keeps its M-Pesa code. Reset it to draft first."))
        if "date" in vals:
            old_months = {l.id: (l.date.year, l.date.month) for l in self}
        result = super().write(vals)
        if "date" in vals:
            for line in self.filtered(lambda l: old_months[l.id] != (l.date.year, l.date.month)):
                line._assign_voucher()
        return result

    def _assign_voucher(self):
        """PCV/2026/11/001: numbered per float and month, in the order typed,
        from the float's gapless sequence. A number is never given twice:
        not to two people saving at once, nor again after a draft is deleted."""
        self.ensure_one()
        number = self.float_id._next_voucher_number(self.date)
        self.with_context(**{SYSTEM_CONTEXT: True}).write({
            "voucher_seq": number,
            "voucher": f"{self.float_id.voucher_prefix}/{self.date.year}/{self.date.month:02d}/{number:03d}",
        })

    @api.constrains("mpesa_code")
    def _check_mpesa_code(self):
        for line in self:
            if line.mpesa_code and not re.fullmatch(r"[A-Z0-9]{10}", line.mpesa_code):
                raise ValidationError(_("%s is not an M-Pesa code. Codes are 10 letters and digits, "
                                        "like UK30R1F8QM.", line.mpesa_code))

    @api.constrains("amount", "charge")
    def _check_amounts(self):
        for line in self:
            if line.amount < 0 or line.charge < 0:
                raise ValidationError(_("Amounts are entered as positive numbers."))

    def unlink(self):
        if self.filtered(lambda l: l.state == "posted"):
            raise UserError(_("Approved vouchers cannot be deleted. Reset them to draft first."))
        return super().unlink()

    # ------------------------------------------------------------ actions
    # An approver need not be an accountant or a storekeeper: once Petty Cash
    # has checked they may approve this float, the entry and the receipt are
    # made with the rights to make them (sudo), still recorded as theirs.
    def action_approve(self):
        for line in self:
            if line.state != "draft":
                continue
            if not line.float_id._can_approve():
                raise UserError(_("You are not an approver for %s.", line.float_id.name))
            if line.status_key != "ready":
                raise UserError(_("%(voucher)s is not ready: %(why)s.", voucher=line.voucher,
                                  why=dict(STATUS)[line.status_key].lower()))
            line = line.with_context(**{SYSTEM_CONTEXT: True})
            if line.needs_stock and not line.picking_id:
                line._receive_stock()
            line.write({"move_id": line._post_entry().id, "state": "posted", "approved_by_id": self.env.user.id,
                        "approved_on": fields.Datetime.now()})
        return True

    def action_reset(self):
        for line in self.filtered(lambda l: l.state == "posted"):
            if not line.float_id._can_approve():
                raise UserError(_("Only an approver for %s can reset an approved voucher.", line.float_id.name))
            if line.picking_id:
                raise UserError(_("%s received stock. Return the goods from Inventory before resetting it.",
                                  line.voucher))
            if line.move_id:
                line.move_id.sudo().button_draft()
                line.move_id.sudo().button_cancel()
            line.with_context(**{SYSTEM_CONTEXT: True}).write(
                {"state": "draft", "move_id": False, "approved_by_id": False, "approved_on": False})
        return True

    def action_cancel(self):
        self.filtered(lambda l: l.state == "draft").with_context(**{SYSTEM_CONTEXT: True}).write(
            {"state": "cancelled"})
        return True

    def action_draft(self):
        """A cancelled voucher back to a draft, its number kept."""
        self.filtered(lambda l: l.state == "cancelled").with_context(**{SYSTEM_CONTEXT: True}).write(
            {"state": "draft"})
        return True

    def _debit_account(self):
        if self.needs_stock and self.product_id:
            categ = self.product_id.categ_id.with_company(self.company_id)
            account = categ.property_stock_valuation_account_id
            if account:
                return account
        return self.account_id

    def _post_entry(self):
        """One journal entry per voucher: the expense (or stock), the M-Pesa
        charge, and the float."""
        self.ensure_one()
        flt = self.float_id
        if not flt.account_id:
            raise UserError(_("Set a journal with a default account on %s.", flt.name))
        label = " ".join(filter(None, [self.voucher, self.description]))
        partner = self.partner_id or self._find_partner()
        lines = []
        if self.kind == "topup":
            lines += [
                Command.create({"name": label, "account_id": flt.account_id.id, "debit": self.amount,
                                "partner_id": partner.id}),
                Command.create({"name": label, "account_id": self.account_id.id, "credit": self.amount,
                                "partner_id": partner.id, "analytic_distribution": self.analytic_distribution}),
            ]
        else:
            lines.append(Command.create({
                "name": label, "account_id": self._debit_account().id, "debit": self.amount,
                "partner_id": partner.id, "analytic_distribution": self.analytic_distribution,
            }))
            if self.charge:
                if not flt.charge_account_id:
                    raise UserError(_("Set the M-Pesa charges account on %s.", flt.name))
                lines.append(Command.create({
                    "name": _("M-PESA CHARGE %s", self.voucher), "account_id": flt.charge_account_id.id,
                    "debit": self.charge, "partner_id": flt.charge_partner_id.id,
                }))
            lines.append(Command.create({"name": label, "account_id": flt.account_id.id,
                                         "credit": self.total, "partner_id": partner.id}))
        move = self.env["account.move"].sudo().create({
            "move_type": "entry", "journal_id": flt.journal_id.id, "date": self.date,
            "ref": " ".join(filter(None, [self.voucher, self.mpesa_code])), "line_ids": lines,
        })
        for attachment in self.env["ir.attachment"].sudo().search(
                [("res_model", "=", self._name), ("res_id", "=", self.id)]):
            attachment.copy({"res_model": "account.move", "res_id": move.id})
        move.action_post()
        return move

    def _find_partner(self):
        if not self.paid_to:
            return self.env["res.partner"]
        return self.env["res.partner"].search(
            [("name", "=ilike", escape_like(self.paid_to)), "|", ("company_id", "=", False),
             ("company_id", "=", self.company_id.id)], limit=1)

    def _receive_stock(self):
        """Receive the goods into the main warehouse at what was paid."""
        self.ensure_one()
        partner = self._find_partner()
        self = self.sudo()  # noqa: PLW0642 - see action_approve
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.company_id.id)], limit=1)
        if not warehouse:
            raise UserError(_("There is no warehouse to receive %s into.", self.product_id.display_name))
        picking_type = warehouse.in_type_id
        unit_price = self.amount / self.quantity
        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": picking_type.default_location_src_id.id or self.env.ref("stock.stock_location_suppliers").id,
            "location_dest_id": picking_type.default_location_dest_id.id,
            "origin": self.voucher,
            "partner_id": partner.id,
            "move_ids": [Command.create({
                "product_id": self.product_id.id, "product_uom_qty": self.quantity,
                "product_uom": self.product_id.uom_id.id, "price_unit": unit_price,
                "location_id": picking_type.default_location_src_id.id or self.env.ref("stock.stock_location_suppliers").id,
                "location_dest_id": picking_type.default_location_dest_id.id,
            })],
        })
        picking.action_confirm()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        result = picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids,
                                      skip_sms=True).button_validate()
        if picking.state != "done":
            raise UserError(_("Odoo could not receive %(product)s for %(voucher)s (%(what)s). Receive it from "
                              "Inventory, then approve.", product=self.product_id.display_name, voucher=self.voucher,
                              what=isinstance(result, dict) and result.get("name") or picking.state))
        # Odoo 19 values a receipt from its documents (bills, orders) or, failing
        # those, a manual value: the amount paid out of the float is that value.
        for move in picking.move_ids:
            move.value_manual = self.amount * (move.quantity / self.quantity if self.quantity else 1)
        self.picking_id = picking

    # ------------------------------------------------------------ book page
    def _book_row(self):
        self.ensure_one()
        return {
            "id": self.id, "date": fields.Date.to_string(self.date), "voucher": self.voucher or "",
            "kind": self.kind, "description": self.description or "", "paid_to": self.paid_to or "",
            "phone": self.phone or "", "method": self.method, "mpesa_code": self.mpesa_code or "",
            "amount": self.amount, "charge": self.charge, "total": self.total,
            "charge_from_statement": self.charge_from_statement, "charge_manual": self.charge_manual,
            "category": self.rule_id.name.removeprefix("Auto: ") if self.rule_id else (
                self.account_id.display_name if self.account_id else ""),
            "rule_id": self.rule_id.id, "account": self.account_id.display_name or "",
            # Names only: cashiers need not see analytic accounting.
            "analytic": ", ".join(self.env["account.analytic.account"].sudo().browse(
                [int(k) for key in (self.analytic_distribution or {}) for k in key.split(",")]).mapped("name")),
            "needs_stock": self.needs_stock,
            "product": [self.product_id.id, self.product_id.display_name] if self.product_id else False,
            "quantity": self.quantity, "uom": self.product_uom_id.name or "",
            "status_key": self.status_key, "status": dict(STATUS)[self.status_key],
            "tone": STATUS_TONE[self.status_key], "state": self.state,
            "approved_by": self.approved_by_id.name or "", "receipts": self.receipt_count,
            "source": self.source, "statement_balance": self.statement_balance or None,
            "move_id": self.move_id.id, "picking_id": self.picking_id.id,
        }

    @api.model
    def book_save(self, line_id, values):
        """Create or update one row from the book page; returns the fresh row."""
        allowed = {"date", "description", "paid_to", "phone", "method", "mpesa_code", "amount", "charge",
                   "rule_id", "product_id", "quantity", "kind", "float_id"}
        values = {k: v for k, v in values.items() if k in allowed}
        if "charge" in values:
            line = self.browse(line_id)
            if line.charge_from_statement:
                # Safaricom's own figure: the statement sets it, not the book.
                values.pop("charge")
            elif values["charge"] is False or values["charge"] is None or values["charge"] == "":
                # Cleared: the tariff fills it in again.
                values["charge_manual"] = False
                if line_id:
                    values["charge"] = line._tariff_charge() or 0.0
                else:
                    values.pop("charge")
            else:
                values["charge_manual"] = True
        if line_id:
            line = self.browse(line_id)
            line.write(values)
        else:
            line = self.create(values)
        row = line._book_row()
        if not line_id and line.amount:
            twin = self.search([
                ("id", "!=", line.id), ("float_id", "=", line.float_id.id), ("date", "=", line.date),
                ("amount", "=", line.amount), ("paid_to", "=ilike", escape_like(line.paid_to)),
                ("state", "!=", "cancelled")], limit=1)
            row["duplicate_of"] = twin.voucher or False
        return row

    @api.model
    def _readable_float(self, float_id):
        """The float, if this user may see it: the suggestions below read the
        table directly, past the record rules."""
        return self.env["odin.petty.cash.float"].search([("id", "=", float_id)], limit=1)

    @api.model
    def book_suggest_payee(self, float_id, text):
        """Payees typed before, with their phone and what they are usually paid for."""
        if not text or not self._readable_float(float_id):
            return []
        self.env.cr.execute("""
            SELECT paid_to, phone, description, method
              FROM odin_petty_cash_line
             WHERE float_id = %s AND paid_to ILIKE %s AND state != 'cancelled'
             ORDER BY date DESC, id DESC LIMIT 400""", (float_id, f"%{text}%"))
        seen = {}
        for paid_to, phone, description, method in self.env.cr.fetchall():
            entry = seen.setdefault(paid_to, {"paid_to": paid_to, "phone": phone or "", "method": method,
                                              "descriptions": Counter(), "count": 0})
            entry["count"] += 1
            if description:
                entry["descriptions"][description] += 1
            if not entry["phone"] and phone:
                entry["phone"] = phone
        result = []
        for entry in sorted(seen.values(), key=lambda e: -e["count"])[:8]:
            top = entry.pop("descriptions").most_common(1)
            entry["usual"] = top[0][0] if top and top[0][1] * 10 >= entry["count"] * 6 else ""
            result.append(entry)
        return result

    @api.model
    def book_suggest_description(self, float_id, text):
        if not text or len(text) < 2 or not self._readable_float(float_id):
            return []
        self.env.cr.execute("""
            SELECT description, COUNT(*) FROM odin_petty_cash_line
             WHERE float_id = %s AND description ILIKE %s AND state != 'cancelled'
             GROUP BY description ORDER BY COUNT(*) DESC LIMIT 8""", (float_id, f"%{text}%"))
        return [row[0] for row in self.env.cr.fetchall()]

    @api.model
    def book_preview_rule(self, float_id, description, kind="payment", paid_to=""):
        """Which category a description would get, before the row is saved."""
        line = self.new({"float_id": float_id, "description": (description or "").upper(), "kind": kind,
                         "paid_to": (paid_to or "").upper()})
        rule = line._match_rule()
        return {"rule_id": rule.id, "category": rule.name.removeprefix("Auto: ") if rule else "",
                "needs_stock": bool(rule and rule in line.float_id.stock_rule_ids)}

    @api.model
    def book_charge(self, amount, phone, method):
        digits = re.sub(r"\D", "", phone or "")
        if method == "mpesa" and len(digits) >= 9 and amount:
            return send_money_charge(float(amount))
        return 0.0

    def book_attach(self, name, datas):
        self.ensure_one()
        attachment = self.env["ir.attachment"].create({
            "name": name, "datas": datas, "res_model": self._name, "res_id": self.id})
        self.message_post(body=_("Receipt attached."), attachment_ids=attachment.ids)
        return self._book_row()

    def book_receipts(self):
        self.ensure_one()
        return [{"id": a.id, "name": a.name, "mimetype": a.mimetype}
                for a in self.env["ir.attachment"].search([("res_model", "=", self._name), ("res_id", "=", self.id)])]

    @api.model
    def book_approve(self, ids):
        lines = self.browse(ids).filtered(lambda l: l.status_key == "ready")
        lines.action_approve()
        return len(lines)


class IrAttachment(models.Model):
    """Keep a voucher's receipt count right however the receipt is added:
    from the book, the chatter, or deleted from either."""

    _inherit = "ir.attachment"

    def _petty_cash_lines(self):
        ids = {a.res_id for a in self if a.res_model == "odin.petty.cash.line" and a.res_id}
        return self.env["odin.petty.cash.line"].browse(ids).exists()

    @api.model_create_multi
    def create(self, vals_list):
        attachments = super().create(vals_list)
        attachments._petty_cash_lines()._refresh_receipt_count()
        return attachments

    def unlink(self):
        lines = self._petty_cash_lines()
        result = super().unlink()
        lines.exists()._refresh_receipt_count()
        return result
