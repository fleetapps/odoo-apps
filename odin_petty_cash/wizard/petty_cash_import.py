import base64
from datetime import timedelta

import pytz

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from ..models.mpesa_statement import read_file
from ..models.petty_cash_line import SYSTEM_CONTEXT


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

    def _to_utc(self, local):
        """Statement times are the phone's local time; Odoo keeps UTC."""
        tz = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "Africa/Nairobi")
        return tz.localize(local).astimezone(pytz.utc).replace(tzinfo=None)

    def action_import(self):
        self.ensure_one()
        try:
            txs = read_file(base64.b64decode(self.statement_file), self.filename, self.password)
        except ValueError as error:
            raise UserError(str(error)) from error
        # Cashiers upload statements: what the statement says about a voucher
        # (code, charge, balance, where it came from) is the statement's to set.
        Line = self.env["odin.petty.cash.line"].with_context(**{SYSTEM_CONTEXT: True})
        flt = self.float_id
        created = confirmed = linked = 0
        used = Line.browse()
        reopened, charge_notes = [], []
        for tx in txs:
            day = tx["time"].date()
            values = {"statement_balance": tx["balance"], "charge_from_statement": True, "charge": tx["charge"]}
            line = Line.search([("float_id", "=", flt.id), ("mpesa_code", "=", tx["code"])], limit=1)
            if line and line.state == "cancelled":
                # The money left the float: the voucher cannot stay cancelled.
                line.action_draft()
                reopened.append(line.voucher)
            elif line:
                confirmed += 1
            else:
                # A voucher typed without its code: same kind and amount,
                # within a day; each voucher answers for one transaction only.
                line = Line.search([
                    ("float_id", "=", flt.id), ("method", "=", "mpesa"), ("mpesa_code", "=", False),
                    ("kind", "=", tx["kind"]), ("state", "!=", "cancelled"), ("id", "not in", used.ids),
                    ("date", ">=", day - timedelta(days=1)), ("date", "<=", day + timedelta(days=1)),
                ], order="date, id").filtered(
                    lambda l: float_compare(l.amount, tx["amount"], precision_digits=2) == 0)[:1]
                if line:
                    values["mpesa_code"] = tx["code"]
                    linked += 1
            if line:
                used |= line
                if line.state == "posted":
                    # Its entry is posted: the charge it posted stays, and a
                    # different one on the statement is reported to fix.
                    if float_compare(line.charge, tx["charge"], precision_digits=2):
                        charge_notes.append(_("%(voucher)s: charges %(book)s on the voucher, %(statement)s on the "
                                              "statement", voucher=line.voucher, book=f"{line.charge:,.2f}",
                                              statement=f"{tx['charge']:,.2f}"))
                    values = {"statement_balance": tx["balance"],
                              **({"mpesa_code": tx["code"]} if not line.mpesa_code else {})}
                values["source"] = "matched"
                if not line.phone and tx["number"] and "*" not in tx["number"]:
                    values["phone"] = tx["number"]
                line.write(values)
                continue
            used |= Line.create({
                "float_id": flt.id, "date": day, "kind": tx["kind"], "method": "mpesa",
                "mpesa_code": tx["code"], "paid_to": tx["party"],
                "phone": tx["number"] if tx["number"] and "*" not in tx["number"] else False,
                "amount": tx["amount"], "source": "statement", **values,
            })
            created += 1
        last = txs[-1]
        last_time = self._to_utc(last["time"])
        newest = not flt.statement_date or last_time >= flt.statement_date
        if newest:
            # An older statement uploaded later does not take the float back.
            flt.sudo().write({"statement_balance": last["balance"], "statement_date": last_time})
        lines = [_(
            "%(confirmed)s already in the book, %(linked)s typed payments got their M-Pesa code, "
            "%(created)s new payments to describe. Statement balance %(balance)s on %(date)s.",
            confirmed=confirmed, linked=linked, created=created,
            balance=f"{last['balance']:,.2f}", date=last["time"].strftime("%d %b %Y %H:%M"))]
        if not newest:
            lines.append(_("This statement is older than the last one uploaded: the float's statement balance "
                           "stays at the newer one."))
        if reopened:
            lines.append(_("Back to draft, as the statement shows they were paid: %s.", ", ".join(reopened)))
        if charge_notes:
            lines.append(_("Approved with a different charge (reset to draft and approve again to correct): %s.",
                           "; ".join(charge_notes)))
        self.write({"state": "done", "result": "\n".join(lines)})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new", "name": _("Statement uploaded")}

    def action_back_to_book(self):
        return {"type": "ir.actions.client", "tag": "odin_petty_cash.book",
                "params": {"float_id": self.float_id.id}}
