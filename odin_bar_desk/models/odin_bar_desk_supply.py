"""Bar Desk: supplier deliveries at the store, checked against the invoice.

Same rules as the rest of the Desk (see odin_bar_desk.py): every call names
the store and carries the staff token; stock actions carry a request id and are
posted once.
"""

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from .odin_bar_desk import LIST_LIMIT


class OdinBarDesk(models.AbstractModel):
    _inherit = "odin.bar.desk"

    # ------------------------------------------------------------------
    # Suppliers
    # ------------------------------------------------------------------

    @api.model
    def _desk_supplier_info(self, partner):
        partner = partner.sudo()
        return {
            "id": partner.id,
            "name": partner.display_name,
            "supplies": partner.bar_supplies or ", ".join(partner.bar_supply_categ_ids.mapped("name")),
        }

    @api.model
    def desk_suppliers(self, bar_id, token, query=""):
        """Suppliers to pick on a supplier delivery, with what they supply."""
        store, _employee = self._desk_store(bar_id, token)
        Partner = self.env["res.partner"].sudo()
        domain = [("company_id", "in", [store.company_id.id, False]), ("supplier_rank", ">", 0)]
        if query:
            domain.append(("name", "ilike", query))
        partners = Partner.search(domain, limit=LIST_LIMIT, order="name")
        if not partners and query:
            partners = Partner.search(
                [("company_id", "in", [store.company_id.id, False]), ("is_company", "=", True), ("name", "ilike", query)],
                limit=20,
                order="name",
            )
        return [self._desk_supplier_info(partner) for partner in partners]

    @api.model
    def desk_supplier_products(self, bar_id, token, partner_id):
        """Products this supplier brings: those of the categories it supplies,
        on its price list, or delivered by it before. Empty when unknown."""
        store, _employee = self._desk_store(bar_id, token)
        partner = self.env["res.partner"].sudo().browse(int(partner_id or 0)).exists()
        products = store._desk_products()
        if not partner:
            return []
        categories = self.env["product.category"].sudo().search(
            [("id", "child_of", partner.bar_supply_categ_ids.ids)]
        ) if partner.bar_supply_categ_ids else self.env["product.category"]
        delivered = self.env["stock.move"].sudo().search(
            [
                ("state", "=", "done"),
                ("picking_id.partner_id", "=", partner.id),
                ("location_dest_id", "=", store.location_id.id),
                ("product_id", "in", products.ids),
            ],
            limit=500,
        ).product_id
        listed = products.filtered(
            lambda product: product.categ_id in categories
            or partner in product.seller_ids.partner_id
            or product in delivered
        )
        return listed.ids

    @api.model
    def _desk_supplier_price(self, product, partner, uom, qty):
        """Price per ``uom`` (e.g. per crate) from the supplier's price list,
        else the product's cost."""
        seller = product._select_seller(
            partner_id=partner, quantity=uom._compute_quantity(qty, product.uom_id), uom_id=product.uom_id
        )
        if seller:
            return seller.product_uom_id._compute_price(seller.price, uom)
        return product.uom_id._compute_price(product.standard_price, uom)

    @api.model
    def _desk_invoice_lines(self, store, lines):
        """Client lines ``[{product_id, uom_id, invoiced, received}]`` as
        ``[(product, uom, invoiced, received)]``."""
        allowed = {product.id: product for product in store._desk_products()}
        Uom = self.env["uom.uom"].sudo()
        result = []
        for line in lines or []:
            product = allowed.get(int(line.get("product_id") or 0))
            if not product:
                raise UserError(_("This product is not offered at %(bar)s.", bar=store.name))
            uom = Uom.browse(int(line.get("uom_id") or product.uom_id.id)).exists()
            if uom != product.uom_id and uom not in product.product_tmpl_id._bar_pack_uoms():
                raise UserError(_("That is not a unit of %(product)s.", product=product.name))
            invoiced = uom.round(float(line.get("invoiced") or 0.0))
            received = uom.round(float(line.get("received") or 0.0))
            if invoiced < 0 or received < 0:
                raise UserError(_("Quantities cannot be negative."))
            if invoiced or received:
                result.append((product, uom, invoiced, received))
        if not result:
            raise UserError(_("Add the items on the invoice."))
        return result

    @api.model
    def _desk_attach(self, records, invoice):
        """Keep the photo or PDF of the supplier's invoice on ``records``."""
        if not invoice or not invoice.get("data"):
            return
        for record in records:
            self.env["ir.attachment"].sudo().create(
                {
                    "name": invoice.get("name") or _("Supplier invoice"),
                    "datas": invoice["data"],
                    "mimetype": invoice.get("mimetype") or False,
                    "res_model": record._name,
                    "res_id": record.id,
                }
            )

    @api.model
    def desk_supplier_delivery(
        self,
        bar_id,
        token,
        uuid,
        partner_id,
        lines,
        missing="coming",
        paid="later",
        amount=False,
        supplier_ref=False,
        invoice=False,
    ):
        """A supplier delivery checked against the supplier's invoice.

        ``lines``: what the invoice lists and what arrived, per item. What
        arrived goes into the store at once. The supplier bill follows the
        invoice. Items short on arrival are either still coming (``missing``
        "coming": they stay expected, already billed) or to be credited
        ("credit": a draft credit note waits for the supplier's). ``paid``
        "now" pays ``amount`` (default: the bill total) from the store's
        account. ``invoice`` is an optional photo or PDF of the invoice.
        """
        store, employee = self._desk_store(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        self._desk_check_payment(store, paid)
        if missing not in ("coming", "credit"):
            raise UserError(_("Say what happens to the missing items."))
        picking_type = store.receipt_type_id
        if not picking_type:
            raise UserError(_("Set the receipt type on %(store)s first.", store=store.name))
        partner = self.env["res.partner"].sudo().browse(int(partner_id or 0)).exists()
        if not partner or partner.company_id not in (store.company_id, self.env["res.company"]):
            raise UserError(_("Pick the supplier."))
        entries = self._desk_invoice_lines(store, lines)
        received = [(product, uom, rec) for product, uom, _inv, rec in entries if rec]
        invoiced = [(product, uom, inv) for product, uom, inv, _rec in entries if inv]
        short = [(product, uom, inv - rec) for product, uom, inv, rec in entries if inv > rec]
        extra = [(product, uom, rec - inv) for product, uom, inv, rec in entries if rec > inv]
        ref = (supplier_ref or "").strip() or False
        activity = self._desk_begin(
            uuid,
            {
                "kind": "receive",
                "bar_id": store.id,
                "partner_id": partner.id,
                "employee_id": employee.id,
                "business_date": store._business_date(),
                "summary": self._desk_summary(received) if received else _("Nothing arrived"),
                "amount": self._desk_value(store, received) if received else 0.0,
                "note": ref,
            },
        )
        source = picking_type.default_location_src_id or partner.property_stock_supplier
        vals = {
            "partner_id": partner.id,
            "origin": ref,
            "bar_employee_id": employee.id,
            "bar_activity_id": activity.id,
        }
        records = [activity]
        if received:
            records.append(self._desk_picking(store, picking_type, source, store.location_id, received, vals))
        if short and missing == "coming":
            pending = self._desk_picking(
                store,
                picking_type,
                source,
                store.location_id,
                short,
                dict(vals, bar_billed=True, origin=_("Still to come on %(ref)s", ref=ref or partner.display_name)),
                validate=False,
            )
            pending.action_confirm()
            records.append(pending)
        notes = []
        if short:
            notes.append(
                _(
                    "Missing on arrival (%(what)s): %(items)s",
                    what=_("still coming") if missing == "coming" else _("credit note due"),
                    items=self._desk_summary(short),
                )
            )
        if extra:
            notes.append(_("More than invoiced arrived: %(items)s", items=self._desk_summary(extra)))
        bill = self.env["account.move"]
        if invoiced:
            bill = self._desk_supplier_bill(
                store, partner, invoiced, paid, ref, activity, amount=amount, notes=notes
            )
            records.append(bill)
        if short and missing == "credit":
            records.append(self._desk_supplier_bill(
                store, partner, short, False, ref, activity, refund=True,
                notes=[_("Waiting for the supplier's credit note for items missing on %(ref)s.", ref=ref or bill.name or "")],
            ))
        self._desk_attach(records, invoice)
        return self._desk_result(activity)

    @api.model
    def _desk_result(self, activity):
        result = super()._desk_result(activity)
        if activity.bill_id:
            result["bill"] = {
                "name": activity.bill_id.name,
                "paid": activity.bill_id.payment_state in ("paid", "in_payment"),
                "checked": activity.bill_id.state == "posted",
            }
        return result

    # ------------------------------------------------------------------
    # Supplier bills and payment
    # ------------------------------------------------------------------

    @api.model
    def _desk_check_payment(self, store, paid):
        if paid not in (None, False, "now", "later"):
            raise UserError(_("Say whether the supplier was paid."))
        if paid == "now" and not store.supplier_payment_journal_id:
            raise UserError(
                _("Ask a manager to set the account suppliers are paid from on %(store)s.", store=store.name)
            )

    @api.model
    def _desk_supplier_bill(
        self, store, partner, lines, paid, supplier_ref, activity, origin=False, amount=False, notes=None, refund=False
    ):
        """Bill ``lines`` ([(product, uom, qty)] or [(product, uom, qty,
        purchase line)]) and, when paid now, pay it from the store's account.

        When the invoice total typed on the Desk (``amount``) does not match
        the bill, the bill waits in draft for a manager to check the prices;
        a payment made now is then recorded on its own, to be matched."""
        company = store.company_id
        today = fields.Date.context_today(self.with_context(tz=store.tz))
        invoice_lines = []
        for line in lines:
            product, uom, qty = line[:3]
            purchase_line = line[3] if len(line) > 3 else False
            if purchase_line:
                vals = purchase_line._prepare_account_move_line()
                vals["quantity"] = uom._compute_quantity(qty, purchase_line.product_uom_id)
            else:
                price = self._desk_supplier_price(product.with_company(company), partner, uom, qty)
                vals = {"product_id": product.id, "quantity": qty, "product_uom_id": uom.id, "price_unit": price}
            invoice_lines.append(Command.create(vals))
        bill = (
            self.env["account.move"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "move_type": "in_refund" if refund else "in_invoice",
                    "partner_id": partner.id,
                    "company_id": company.id,
                    "invoice_date": today,
                    "ref": (supplier_ref or "").strip() or False,
                    "invoice_origin": origin or False,
                    "invoice_line_ids": invoice_lines,
                }
            )
        )
        amount = float(amount or 0.0)
        mismatch = amount and bill.currency_id.compare_amounts(amount, bill.amount_total) != 0
        for note in notes or []:
            bill.message_post(body=note)
        if refund:
            return bill
        if mismatch:
            bill.message_post(
                body=_(
                    "The supplier's invoice says %(invoice)s but Odoo's prices give %(odoo)s. "
                    "Check the prices, then confirm this bill.",
                    invoice=amount,
                    odoo=bill.amount_total,
                )
            )
        else:
            bill.action_post()
        if paid == "now":
            journal = store.supplier_payment_journal_id
            if bill.state == "posted":
                self.env["account.payment.register"].sudo().with_company(company).with_context(
                    active_model="account.move", active_ids=bill.ids
                ).create({"journal_id": journal.id, "payment_date": today})._create_payments()
            else:
                payment = self.env["account.payment"].sudo().with_company(company).create(
                    {
                        "payment_type": "outbound",
                        "partner_type": "supplier",
                        "partner_id": partner.id,
                        "amount": amount,
                        "journal_id": journal.id,
                        "date": today,
                    }
                )
                payment.action_post()
        activity.bill_id = bill
        return bill
