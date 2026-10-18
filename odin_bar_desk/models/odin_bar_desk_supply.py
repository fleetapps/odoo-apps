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
        return {"ids": listed.ids, "prices": self._desk_supplier_prices(store, partner, listed)}

    @api.model
    def _desk_supplier_prices(self, store, partner, products):
        """``{"<product>:<uom>": price}`` for every unit a delivery can be
        entered in, so the Desk opens on what this supplier last charged and
        the usual case is a glance rather than typing."""
        company = store.company_id
        prices = {}
        for product in products.with_company(company):
            for uom in [product.uom_id] + list(product.product_tmpl_id._bar_pack_uoms()):
                prices[f"{product.id}:{uom.id}"] = self._desk_supplier_price(product, partner, uom, 1)
        return prices

    @api.model
    def _desk_supplier_price(self, product, partner, uom, qty):
        """Price per ``uom`` (e.g. per crate): the supplier's price list, else
        what this supplier last billed for it, else the product's cost.

        The middle rung matters more than it looks. Most products here have no
        price list entry at all, and a cost that has never been set -- so
        without it the Desk would suggest zero, which is how bills totalling
        nothing came to be posted in the first place."""
        seller = product._select_seller(
            partner_id=partner, quantity=uom._compute_quantity(qty, product.uom_id), uom_id=product.uom_id
        )
        if seller:
            return seller.product_uom_id._compute_price(seller.price, uom)
        billed = self.env["account.move.line"].sudo().search(
            [
                ("move_id.move_type", "=", "in_invoice"),
                ("move_id.state", "=", "posted"),
                ("move_id.partner_id", "=", partner.id),
                ("product_id", "=", product.id),
                ("price_unit", ">", 0),
            ],
            order="date desc, id desc",
            limit=1,
        )
        if billed:
            return (billed.product_uom_id or product.uom_id)._compute_price(billed.price_unit, uom)
        return product.uom_id._compute_price(product.standard_price, uom)

    @api.model
    def _desk_purchase_order(self, store, partner, picking_type, entries, ref):
        """The supplier's invoice as a confirmed purchase order.

        The Desk books goods straight into the store, but it books them
        through a purchase order all the same. Only a stock move that
        carries a ``purchase_line_id`` is re-valued when the bill is posted
        (``purchase_stock``'s ``_get_value_from_account_move`` returns
        nothing without one), and that re-valuation is what keeps the
        average cost in step with what the club actually paid. Receiving
        without an order leaves every delivery valued at the cost the
        product already had, whatever the invoice said.

        Ordered quantity is what the invoice lists, falling back to what
        arrived for anything delivered but not invoiced. Receiving less
        leaves a backorder; receiving more is allowed.
        """
        company = store.company_id
        # Purchase lines created through a nested Command on the order carry no
        # order_id yet, so Odoo's own _prepare_add_missing_fields never runs and
        # tax_ids would be left empty -- a bill with no VAT on it at all. Map the
        # taxes here, exactly as _compute_tax_id would.
        fiscal_position = self.env["account.fiscal.position"].sudo().with_company(
            company
        )._get_fiscal_position(partner)

        def taxes_for(product):
            taxes = product.sudo().supplier_taxes_id._filter_taxes_by_company(company)
            return fiscal_position.map_tax(taxes)

        Order = self.env["purchase.order"].sudo().with_company(company)
        order = Order.create(
            {
                "partner_id": partner.id,
                "company_id": company.id,
                "picking_type_id": picking_type.id,
                "partner_ref": ref or False,
                "order_line": [
                    Command.create(
                        {
                            # Explicit sequence so order_line comes back in the
                            # order the invoice was entered: the caller pairs
                            # lines with entries by position.
                            "sequence": index * 10,
                            "product_id": product.id,
                            "name": product.display_name,
                            "product_qty": invoiced or received,
                            "product_uom_id": uom.id,
                            "price_unit": (
                                price
                                if price is not None
                                else self._desk_supplier_price(
                                    product.with_company(company), partner, uom, invoiced or received
                                )
                            ),
                            "date_planned": fields.Datetime.now(),
                            "tax_ids": [Command.set(taxes_for(product).ids)],
                        }
                    )
                    for index, (product, uom, invoiced, received, price) in enumerate(entries)
                ],
            }
        )
        order.button_confirm()
        if order.state not in ("purchase", "done"):
            # Two-step purchase approval is not a thing the Desk can wait for:
            # the goods are already on the counter.
            order.button_approve(force=True)
        return order

    @api.model
    def _desk_invoice_lines(self, store, lines):
        """Client lines ``[{product_id, uom_id, invoiced, received, price}]``
        as ``[(product, uom, invoiced, received, price)]``.

        ``price`` is what the supplier charged for one ``uom`` on this
        invoice. It is what the goods end up valued at, so it is taken from
        the invoice in front of the staff member rather than from a price
        list that may be months old. ``None`` when the Desk did not ask for
        one, and then the supplier's price list stands in."""
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
            raw_price = line.get("price")
            price = None if raw_price in (None, False, "") else float(raw_price)
            if price is not None and price < 0:
                raise UserError(_("A price cannot be negative."))
            if invoiced or received:
                result.append((product, uom, invoiced, received, price))
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
        received = [(product, uom, rec) for product, uom, _inv, rec, _price in entries if rec]
        short = [(product, uom, inv - rec) for product, uom, inv, rec, _price in entries if inv > rec]
        extra = [(product, uom, rec - inv) for product, uom, inv, rec, _price in entries if rec > inv]
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
        order = self._desk_purchase_order(store, partner, picking_type, entries, ref)
        desk_vals = {"bar_employee_id": employee.id, "bar_activity_id": activity.id}
        records = [activity, order]
        picking = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))[:1]
        if picking:
            picking.write(desk_vals)
            # The order follows the invoice, so a line's received quantity is
            # already in the move's own unit -- no conversion here.
            wanted = {line.id: rec for (_p, _u, _inv, rec, _price), line in zip(entries, order.order_line)}
            self._desk_validate(
                picking,
                {move.id: wanted.get(move.purchase_line_id.id, 0.0) for move in picking.move_ids},
            )
            records.append(picking)
            rest = picking.backorder_ids.filtered(lambda b: b.state not in ("done", "cancel"))
            if rest:
                rest.write(desk_vals)
                if missing == "credit":
                    rest.action_cancel()
                else:
                    # Billed on this invoice already, so receiving the rest
                    # later must not bill it a second time.
                    rest.bar_billed = True
                    records.append(rest)
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
        invoiced = [
            (product, uom, inv, line, price)
            for (product, uom, inv, _rec, price), line in zip(entries, order.order_line)
            if inv
        ]
        if invoiced:
            bill = self._desk_supplier_bill(
                store, partner, invoiced, paid, ref, activity,
                origin=order.name, amount=amount, notes=notes,
            )
            records.append(bill)
        if short and missing == "credit":
            credit = [
                (product, uom, inv - rec, line, price)
                for (product, uom, inv, rec, price), line in zip(entries, order.order_line)
                if inv > rec
            ]
            records.append(self._desk_supplier_bill(
                store, partner, credit, False, ref, activity, refund=True,
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
        """Bill ``lines`` ([(product, uom, qty)], or with a purchase line and
        the invoice price appended) and, when paid now, pay it from the
        store's account.

        When the invoice total typed on the Desk (``amount``) does not match
        the bill, the bill waits in draft for a manager to check the prices,
        and nothing is paid. Paying a bill that is still in draft cannot be
        reconciled against it, so it would leave the money recorded and the
        bill still showing as owed -- which is how every bill on the system
        came to read "not paid"."""
        company = store.company_id
        today = fields.Date.context_today(self.with_context(tz=store.tz))
        invoice_lines = []
        for line in lines:
            product, uom, qty = line[:3]
            purchase_line = line[3] if len(line) > 3 else False
            price = line[4] if len(line) > 4 else None
            if purchase_line:
                vals = purchase_line._prepare_account_move_line()
                vals["quantity"] = uom._compute_quantity(qty, purchase_line.product_uom_id)
            else:
                if price is None:
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
        # No total typed means there is nothing to check the bill against --
        # the order's own prices stand. A typed zero is a real figure and is
        # checked like any other, rather than quietly skipping the check.
        total = None if amount in (None, False, "") else float(amount)
        mismatch = total is not None and bill.currency_id.compare_amounts(total, bill.amount_total) != 0
        for note in notes or []:
            bill.message_post(body=note)
        if refund:
            return bill
        if mismatch:
            bill.message_post(
                body=_(
                    "The supplier's invoice says %(invoice)s but the prices entered give %(odoo)s. "
                    "Check the prices, then confirm this bill.",
                    invoice=total,
                    odoo=bill.amount_total,
                )
            )
        else:
            bill.action_post()
        if paid == "now" and bill.state == "posted":
            self.env["account.payment.register"].sudo().with_company(company).with_context(
                active_model="account.move", active_ids=bill.ids
            ).create({"journal_id": store.supplier_payment_journal_id.id, "payment_date": today})._create_payments()
        elif paid == "now":
            bill.message_post(
                body=_(
                    "Marked paid on the Desk, but the bill is still to be checked, so no payment "
                    "has been recorded. Confirm the bill and then register the payment."
                )
            )
        activity.bill_id = bill
        return bill
