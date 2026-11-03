import base64
from datetime import timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from ..models.mpesa_statement import read_file


class PettyCashImport(models.TransientModel):
    """Upload the weekly M-Pesa statement. Each transaction confirms the
    voucher already typed for it (same code, or same amount within a day),
    or becomes a new row for someone to describe."""

    _name = "odin.petty.cash.import"
    _description = "Upload M-Pesa statement"

    float_id = fields.Many2one("odin.petty.cash.float", required=True)
    statement_file = fields.Binary(string="Statement", required=True)
    filename = fields.Char()
    password = fields.Char(help="Safaricom protects statement PDFs, usually with the ID number.")
    result = fields.Text(readonly=True)
    state = fields.Selection([("upload", "Upload"), ("done", "Done")], default="upload")

    def action_import(self):
        self.ensure_one()
        try:
            txs = read_file(base64.b64decode(self.statement_file), self.filename, self.password)
        except ValueError as error:
            raise UserError(str(error)) from error
        Line = self.env["odin.petty.cash.line"]
        flt = self.float_id
        created = confirmed = linked = 0
        for tx in txs:
            day = tx["time"].date()
            values = {"statement_balance": tx["balance"], "charge_from_statement": True, "charge": tx["charge"]}
            line = Line.search([("float_id", "=", flt.id), ("mpesa_code", "=", tx["code"])], limit=1)
            if line:
                confirmed += 1
            else:
                line = Line.search([
                    ("float_id", "=", flt.id), ("method", "=", "mpesa"), ("mpesa_code", "=", False),
                    ("kind", "=", tx["kind"]), ("state", "!=", "cancelled"),
                    ("date", ">=", day - timedelta(days=1)), ("date", "<=", day + timedelta(days=1)),
                ], order="date, id").filtered(
                    lambda l: float_compare(l.amount, tx["amount"], precision_digits=2) == 0)[:1]
                if line:
                    values["mpesa_code"] = tx["code"]
                    linked += 1
            if line:
                if line.state == "posted":
                    values = {"statement_balance": tx["balance"]}
                values["source"] = "matched"
                if not line.phone and tx["number"] and "*" not in tx["number"]:
                    values["phone"] = tx["number"]
                line.write(values)
                continue
            Line.create({
                "float_id": flt.id, "date": day, "kind": tx["kind"], "method": "mpesa",
                "mpesa_code": tx["code"], "paid_to": tx["party"],
                "phone": tx["number"] if tx["number"] and "*" not in tx["number"] else False,
                "amount": tx["amount"], "source": "statement", **values,
            })
            created += 1
        last = txs[-1]
        flt.write({"statement_balance": last["balance"], "statement_date": last["time"]})
        self.write({"state": "done", "result": _(
            "%(confirmed)s already in the book, %(linked)s typed payments got their M-Pesa code, "
            "%(created)s new payments to describe. Statement balance %(balance)s on %(date)s.",
            confirmed=confirmed, linked=linked, created=created,
            balance=f"{last['balance']:,.2f}", date=last["time"].strftime("%d %b %Y %H:%M"))})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new", "name": _("Statement uploaded")}

    def action_back_to_book(self):
        return {"type": "ir.actions.client", "tag": "odin_petty_cash.book",
                "params": {"float_id": self.float_id.id}}
