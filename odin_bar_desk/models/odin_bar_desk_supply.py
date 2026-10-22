"""Bar Desk: supplier deliveries at the store, checked against the invoice.

Same rules as the rest of the Desk (see odin_bar_desk.py): every call names
the store and carries the staff token; stock actions carry a request id and are
posted once.
"""

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

from .odin_bar_desk import LIST_LIMIT

# How a supplier's invoice shows VAT. Staff type the price printed on the
# invoice, so the bill has to read it the same way or every total is out by
# the VAT -- and with the bill posted and paid in the same tap, nobody would
# be there to notice.
VAT_MODES = ("added", "included", "none")
# A price this many times the last one, or under its inverse, is flagged on
# the bill for a manager: most often a zero typed too many or too few.
PRICE_JUMP = 1.5
NEW_PRODUCT_NAME_MAX = 80


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
            "vat": partner.bar_invoice_vat or "included",
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
        """Products this supplier brings (those of the categories it supplies,
        on its price list, or delivered by it before) and, for every product
        on the Desk, the price to suggest."""
        store, _employee = self._desk_store(bar_id, token)
        partner = self.env["res.partner"].sudo().browse(int(partner_id or 0)).exists()
        if not partner:
            return {"ids": [], "prices": {}}
        products = store._desk_products()
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
        # Prices for every product, not only this supplier's: "Other
        # products" is one tap away, and a blank there is how a zero gets in.
        return {"ids": listed.ids, "prices": self._desk_supplier_prices(store, partner, products)}

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    @api.model
    def _desk_last_prices(self, company, products, partner=None):
        """``{product id: price per product unit}`` last paid, before VAT:
        by ``partner`` when given, else by anyone. Confirmed purchase lines
        and posted vendor bills, newest first.

        Not the supplier price list: confirming an order adds the supplier
        there at that first price and never updates it, so after one delivery
        it would suggest the same old price for ever."""
        if not products:
            return {}
        by_order = SQL("AND o.partner_id = %s", partner.id) if partner else SQL()
        by_bill = SQL("AND m.partner_id = %s", partner.id) if partner else SQL()
        rows = self.env.execute_query(SQL(
            """
            SELECT DISTINCT ON (product_id) product_id, uom_id, unit_price
              FROM (
                    SELECT l.product_id, l.product_uom_id AS uom_id,
                           l.price_subtotal / l.product_qty AS unit_price,
                           o.date_order AS day, l.id AS tie
                      FROM purchase_order_line l
                      JOIN purchase_order o ON o.id = l.order_id
                     WHERE o.company_id = %(company)s
                       AND o.state IN ('purchase', 'done')
                       AND o.currency_id = %(currency)s
                       AND l.product_id IN %(products)s
                       AND l.product_qty > 0 AND l.price_subtotal > 0
                       %(by_order)s
                 UNION ALL
                    SELECT l.product_id, l.product_uom_id,
                           l.balance / l.quantity,
                           m.invoice_date::timestamp, -l.id
                      FROM account_move_line l
                      JOIN account_move m ON m.id = l.move_id
                     WHERE m.company_id = %(company)s
                       AND m.move_type = 'in_invoice'
                       AND m.state = 'posted'
                       AND l.display_type = 'product'
                       AND l.product_id IN %(products)s
                       AND l.quantity > 0 AND l.balance > 0
                       %(by_bill)s
                   ) prices
             ORDER BY product_id, day DESC NULLS LAST, tie DESC
            """,
            company=company.id,
            currency=company.currency_id.id,
            products=tuple(products.ids),
            by_order=by_order,
            by_bill=by_bill,
        ))
        Uom = self.env["uom.uom"].sudo()
        Product = self.env["product.product"].sudo()
        return {
            product_id: Uom.browse(uom_id)._compute_price(unit_price, Product.browse(product_id).uom_id)
            if uom_id else unit_price
            for product_id, uom_id, unit_price in rows
        }

    @api.model
    def _desk_listed_price(self, product, partner):
        """This supplier's price list price per product unit, or None."""
        seller = product._select_seller(partner_id=partner, uom_id=product.uom_id)
        if not seller:
            return None
        return seller.product_uom_id._compute_price(seller.price, product.uom_id)

    @api.model
    def _desk_supplier_prices(self, store, partner, products):
        """``{"<product>:<uom>": price}``, before VAT, for every unit a
        delivery can be entered in, so the Desk opens on what this supplier
        last charged and the usual case is a glance rather than typing.

        What this supplier last charged, else its price list, else what anyone
        last charged, else the product's cost. Most products here have no
        price list entry and a cost that was never set, so without the first
        rung the Desk would suggest zero -- which is how bills totalling
        nothing came to be posted in the first place."""
        company = store.company_id
        products = products.with_company(company)
        own = self._desk_last_prices(company, products, partner)
        anyone = self._desk_last_prices(company, products.filtered(lambda p: p.id not in own))
        prices = {}
        for product in products:
            unit_price = own.get(product.id)
            if unit_price is None:
                unit_price = self._desk_listed_price(product, partner)
            if unit_price is None:
                unit_price = anyone.get(product.id, product.standard_price)
            for uom in [product.uom_id] + list(product.product_tmpl_id._bar_pack_uoms()):
                prices[f"{product.id}:{uom.id}"] = company.currency_id.round(
                    product.uom_id._compute_price(unit_price, uom)
                )
        return prices

    @api.model
    def _desk_supplier_price(self, store, product, partner, uom):
        """The price :meth:`_desk_supplier_prices` suggests for one product
        and unit: what a Desk that sent no price is taken to have meant."""
        return self._desk_supplier_prices(store, partner, product).get(f"{product.id}:{uom.id}", 0.0)

    # ------------------------------------------------------------------
    # Taxes
    # ------------------------------------------------------------------

    @api.model
    def _desk_included_tax(self, tax):
        """The same tax, read as included in the price.

        Converting a VAT-inclusive price to a net one by hand rounds: 1,000
        including 16% is 862.07 net, and six of those bill 6,000.01, which
        M-Pesa never paid. A price-included tax bills exactly what the
        invoice says. Made the first time a supplier invoices that way."""
        if tax.price_include or tax.amount_type != "percent":
            return tax
        Tax = self.env["account.tax"].sudo().with_company(tax.company_id)
        twin = Tax.search(
            [
                ("company_id", "=", tax.company_id.id),
                ("type_tax_use", "=", tax.type_tax_use),
                ("amount_type", "=", "percent"),
                ("amount", "=", tax.amount),
                ("tax_group_id", "=", tax.tax_group_id.id),
                ("price_include_override", "=", "tax_included"),
            ],
            limit=1,
        )
        return twin or tax.sudo().copy(
            {"name": _("%(tax)s (included)", tax=tax.name), "price_include_override": "tax_included"}
        )

    @api.model
    def _desk_line_taxes(self, company, partner, product, vat):
        """Taxes for one line of an invoice that shows VAT the ``vat`` way,
        mapped exactly as purchase lines map them (_compute_tax_id)."""
        if vat == "none":
            return self.env["account.tax"]
        fiscal_position = self.env["account.fiscal.position"].sudo().with_company(
            company
        )._get_fiscal_position(partner)
        taxes = fiscal_position.map_tax(product.sudo().supplier_taxes_id._filter_taxes_by_company(company))
        if vat == "included":
            taxes = self.env["account.tax"].union(*(self._desk_included_tax(tax) for tax in taxes))
        return taxes

    # ------------------------------------------------------------------
    # New products, added while receiving
    # ------------------------------------------------------------------

    @api.model
    def _desk_new_product(self, store, partner, data, employee):
        """A product the store has never stocked, named by staff on the
        invoice in front of them.

        It is shaped like the products already in its category -- tots with a
        bottle size for spirits, units with a crate for beer -- because those
        are the units the Desk can count, and staff only pick from what the
        category already uses. The selling price is asked for too, so the POS
        import can check prices against it from the first sale. It is flagged
        for a manager, who links it to its POS item and checks the rest.

        A name the Desk already has (any case, any spacing) is that product:
        the same item typed on two invoices before the Desk reloaded must not
        become two products."""
        data = data or {}
        name = " ".join(str(data.get("name") or "").split())
        if not name:
            raise UserError(_("Name the new product."))
        if len(name) > NEW_PRODUCT_NAME_MAX:
            raise UserError(_("That name is too long. Keep it under %(max)s letters.", max=NEW_PRODUCT_NAME_MAX))
        products = store._desk_products()
        same = products.filtered(lambda product: (product.name or "").casefold() == name.casefold())
        if same:
            return same[:1]
        category = self.env["product.category"].sudo().browse(int(data.get("categ_id") or 0)).exists()
        if not category or category not in products.categ_id:
            raise UserError(_("Pick the category of %(name)s.", name=name))
        uom = self.env["uom.uom"].sudo().browse(int(data.get("uom_id") or 0)).exists()
        siblings = products.filtered(lambda product: product.categ_id == category)
        if not uom or uom not in siblings.uom_id:
            raise UserError(_("Pick how %(name)s is counted.", name=name))
        pack = self.env["uom.uom"].sudo().browse(int(data.get("pack_uom_id") or 0)).exists()
        packs = self.env["uom.uom"].union(*(product.product_tmpl_id._bar_pack_uoms() for product in siblings))

        def root(unit):
            return (unit.parent_path or f"{unit.id}/").split("/")[0]

        if pack and (pack not in packs or root(pack) != root(uom) or pack.factor <= uom.factor):
            raise UserError(_("That is not a unit %(name)s can come in.", name=name))
        try:
            sale_price = float(data.get("sale_price") or 0.0)
        except (TypeError, ValueError):
            sale_price = 0.0
        if sale_price <= 0:
            raise UserError(_("Type the selling price of %(name)s.", name=name))
        company = store.company_id
        template = (
            self.env["product.template"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "name": name,
                    "type": "consu",
                    "is_storable": True,
                    "categ_id": category.id,
                    "uom_id": uom.id,
                    "uom_ids": [Command.set(pack.ids)],
                    "list_price": sale_price,
                    "purchase_ok": True,
                    "sale_ok": True,
                    "bar_desk_ok": True,
                    "bar_desk_new": True,
                    "company_id": company.id,
                }
            )
        )
        template.message_post(
            body=_(
                "Added on the Bar Desk by %(who)s, receiving from %(supplier)s. "
                "Check it, link it to its POS item, then clear Added on the Desk.",
                who=employee.name,
                supplier=partner.display_name,
            )
        )
        return template.product_variant_id

    # ------------------------------------------------------------------
    # The delivery
    # ------------------------------------------------------------------

    @api.model
    def _desk_purchase_order(self, store, partner, picking_type, entries, ref, vat="included"):
        """The supplier's invoice as a confirmed purchase order.

        The Desk books goods straight into the store, but it books them
        through a purchase order all the same. Odoo values an incoming move
        from its bill, else from its order line, else from the product's
        cost -- and the first two only exist for a move that carries a
        ``purchase_line_id``. Receiving without an order leaves every
        delivery valued at the cost the product already had, whatever the
        invoice said, and the cost never moves.

        Ordered quantity is what the invoice lists, falling back to what
        arrived for anything delivered but not invoiced. Receiving less
        leaves a backorder; receiving more is allowed.
        """
        company = store.company_id
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
                            "price_unit": price,
                            "date_planned": fields.Datetime.now(),
                            # A line created through a nested Command has no
                            # order_id yet, so Odoo's _prepare_add_missing_fields
                            # never maps its taxes: without this the bill would
                            # carry no VAT at all.
                            "tax_ids": [Command.set(self._desk_line_taxes(company, partner, product, vat).ids)],
                        }
                    )
                    for index, (product, uom, invoiced, received, price) in enumerate(entries)
                ],
            }
        )
        order.button_confirm()
        if order.state == "to approve":
            # Two-step approval is not a thing the Desk can wait for: the goods
            # are already on the counter. Odoo 19's button_approve quietly drops
            # an order the signed-in user may not approve, whatever ``force``
            # says, so no receipt would ever be made. Approve it the way
            # button_approve does.
            order.write({"state": "purchase", "date_approve": fields.Datetime.now()})
            order.filtered(lambda o: o.lock_confirmed_po == "lock").locked = True
            order._create_picking()
        return order

    @api.model
    def _desk_invoice_lines(self, store, partner, lines, employee):
        """Client lines ``[{product_id, uom_id, invoiced, received, price}]``
        as ``[(product, uom, invoiced, received, price)]``. A line may name a
        product the store has never stocked with ``new_product`` instead of
        ``product_id`` (see :meth:`_desk_new_product`).

        ``price`` is what the supplier charged for one ``uom`` on this
        invoice, read the way the invoice shows VAT. It is what the goods end
        up valued at, so it is taken from the invoice in front of the staff
        member rather than from a price list that may be months old, and an
        invoiced line without one is refused: a zero here is stock that costs
        nothing and a bill that is short. A Desk that sent no price at all
        (one from before prices were asked) gets the suggested one."""
        allowed = {product.id: product for product in store._desk_products()}
        Uom = self.env["uom.uom"].sudo()
        result = []
        for line in lines or []:
            if line.get("new_product") and not line.get("product_id"):
                product = self._desk_new_product(store, partner, line["new_product"], employee)
                allowed[product.id] = product
            else:
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
            if not (invoiced or received):
                continue
            raw_price = line.get("price")
            # Not ``in (None, False, "")``: 0 == False, and a zero typed on
            # the Desk would pass for no price at all.
            price = None if raw_price is None or raw_price is False or raw_price == "" else float(raw_price)
            if price is not None and price < 0:
                raise UserError(_("A price cannot be negative."))
            if price is None:
                price = self._desk_supplier_price(store, product, partner, uom)
            if invoiced and store.company_id.currency_id.is_zero(price):
                raise UserError(
                    _("Type what %(supplier)s charged for %(product)s: the invoice has no free items.",
                      supplier=partner.display_name, product=product.display_name)
                )
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
    def _desk_price_notes(self, store, order, before):
        """What changed against what this supplier charged last time, for the
        bill's chatter: a manager reading it sees every price rise at once,
        and the ones out of all proportion are called out."""
        currency = store.company_id.currency_id
        changed, jumps = [], []
        for line in order.order_line:
            product = line.product_id
            last = before.get(product.id)
            if not last or not line.product_qty:
                continue
            now = line._get_stock_move_price_unit()
            if currency.compare_amounts(now, last) == 0 or abs(now - last) <= last * 0.005:
                continue
            per = line.product_uom_id
            text = _(
                "%(product)s: %(now)s a %(unit)s, was %(last)s",
                product=product.display_name,
                now=currency.format(product.uom_id._compute_price(now, per)),
                unit=per.name,
                last=currency.format(product.uom_id._compute_price(last, per)),
            )
            changed.append(text)
            if now > last * PRICE_JUMP or now < last / PRICE_JUMP:
                jumps.append(text)
        notes = []
        if jumps:
            notes.append(_("Check these prices, far from what this supplier charged last time: %(items)s",
                           items="; ".join(jumps)))
        if changed:
            notes.append(_("New prices on this invoice (before VAT): %(items)s", items="; ".join(changed)))
        return notes

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
        vat=False,
    ):
        """A supplier delivery checked against the supplier's invoice.

        ``lines``: what the invoice lists and what arrived, per item, with the
        price per unit as printed. ``vat`` says how the invoice shows VAT
        ("included", "added" or "none"; default: what this supplier used
        last, else included). What arrived goes into the store at once, valued at those
        prices. The supplier bill follows the invoice and is posted. Items
        short on arrival are either still coming (``missing`` "coming": they
        stay expected, already billed) or to be credited ("credit": a draft
        credit note waits for the supplier's). ``paid`` "now" pays the bill
        from the store's account. ``invoice`` is an optional photo or PDF of
        the invoice.
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
        vat = vat or partner.bar_invoice_vat or "included"
        if vat not in VAT_MODES:
            raise UserError(_("Say how the invoice shows VAT."))
        company = store.company_id
        ref = (supplier_ref or "").strip() or False
        self._desk_check_new_invoice(company, partner, ref)
        entries = self._desk_invoice_lines(store, partner, lines, employee)
        received = [(product, uom, rec) for product, uom, _inv, rec, _price in entries if rec]
        short = [(product, uom, inv - rec) for product, uom, inv, rec, _price in entries if inv > rec]
        extra = [(product, uom, rec - inv) for product, uom, inv, rec, _price in entries if rec > inv]
        activity = self._desk_begin(
            uuid,
            {
                "kind": "receive",
                "bar_id": store.id,
                "partner_id": partner.id,
                "employee_id": employee.id,
                "business_date": store._business_date(),
                "summary": self._desk_summary(received) if received else _("Nothing arrived"),
                "note": ref,
            },
        )
        products = self.env["product.product"].union(*(product for product, *_rest in entries)).with_company(company)
        last_prices = self._desk_last_prices(company, products, partner)
        costs_before = {product.id: product.standard_price for product in products}
        order = self._desk_purchase_order(store, partner, picking_type, entries, ref, vat)
        # The price paid, before VAT, per product unit: what the goods are
        # valued at, whether or not they have arrived yet.
        unit_costs = {line.id: line._get_stock_move_price_unit() for line in order.order_line}
        for line in order.order_line.filtered(lambda line: line.product_id.bar_desk_new
                                              and not line.product_id.standard_price):
            # A product added just now has no cost until it arrives; give it
            # the one on the invoice, without a revaluation of stock it has
            # never had.
            line.product_id.with_context(disable_auto_revaluation=True).standard_price = unit_costs[line.id]
        activity.amount = company.currency_id.round(sum(
            line.product_uom_id._compute_quantity(rec, line.product_id.uom_id) * unit_costs[line.id]
            for (_p, _u, _inv, rec, _price), line in zip(entries, order.order_line)
        ))
        desk_vals = {"bar_employee_id": employee.id, "bar_activity_id": activity.id}
        records = [activity, order]
        picking = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))[:1]
        if picking:
            picking.write(desk_vals)
            # Received quantities follow the invoice, in its units (crates);
            # Odoo books the receipt in the product's own (bottles).
            wanted = {}
            for (_p, _u, _inv, rec, _price), line in zip(entries, order.order_line):
                move = picking.move_ids.filtered(lambda m, line=line: m.purchase_line_id == line)[:1]
                if move:
                    wanted[move.id] = line.product_uom_id._compute_quantity(rec, move.product_uom)
            self._desk_validate(picking, wanted)
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
        notes = self._desk_price_notes(store, order, last_prices)
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
        if partner.bar_invoice_vat != vat:
            partner.bar_invoice_vat = vat
        self._desk_attach(records, invoice)
        result = self._desk_result(activity)
        result["new_costs"] = [
            product.display_name
            for product in products
            if costs_before[product.id]
            and company.currency_id.compare_amounts(product.standard_price, costs_before[product.id]) != 0
        ]
        return result

    @api.model
    def _desk_check_new_invoice(self, company, partner, ref):
        """Refuse an invoice this supplier has already been billed for.

        Odoo only flags a repeated vendor reference; it posts it all the same.
        With the bill posted and paid in the same tap, the same invoice
        entered twice -- on two phones, or again the next morning -- would pay
        the supplier twice from M-Pesa."""
        if not ref:
            return
        pattern = ref.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        earlier = self.env["account.move"].sudo().search(
            [
                ("company_id", "=", company.id),
                ("move_type", "=", "in_invoice"),
                ("state", "!=", "cancel"),
                ("commercial_partner_id", "=", partner.commercial_partner_id.id),
                ("ref", "=ilike", pattern),
            ],
            limit=1,
        )
        if earlier:
            raise UserError(
                _(
                    "Invoice %(ref)s from %(supplier)s is already in: %(bill)s of %(date)s. "
                    "If this is another invoice, check its number.",
                    ref=ref,
                    supplier=partner.display_name,
                    bill=earlier.display_name,
                    date=self._desk_day_label(earlier.invoice_date or earlier.date),
                )
            )

    @api.model
    def _desk_result(self, activity):
        result = super()._desk_result(activity)
        if activity.bill_id:
            bill = activity.bill_id
            result["bill"] = {
                "name": bill.name,
                "paid": bill.payment_state in ("paid", "in_payment"),
                "checked": bill.state == "posted",
                "total": bill.amount_total,
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
        the invoice price appended), post it and, when paid now, pay it from
        the store's account.

        ``amount`` is only sent by a Desk from before prices were typed line
        by line: the total typed there. When it does not match the bill, the
        bill waits in draft for a manager to check the prices, and nothing is
        paid. Paying a bill that is still in draft cannot be reconciled
        against it, so it would leave the money recorded and the bill still
        showing as owed -- which is how every bill on the system came to read
        "not paid"."""
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
                    price = self._desk_supplier_price(store, product, partner, uom)
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
            wizard = (
                self.env["account.payment.register"]
                .sudo()
                .with_company(company)
                .with_context(active_model="account.move", active_ids=bill.ids)
                .create({"journal_id": store.supplier_payment_journal_id.id, "payment_date": today})
            )
            # Odoo 19 books a payment only when its method has an outstanding
            # account; without one the payment waits for a bank statement to
            # match it, and a bar paying by M-Pesa never imports one -- so the
            # books keep owing a supplier who was paid. Book it to Outstanding
            # Payments instead, which also settles the bill.
            if not wizard.payment_method_line_id.payment_account_id:
                wizard = wizard.with_context(force_payment_move=True)
            wizard._create_payments()
        elif paid == "now":
            bill.message_post(
                body=_(
                    "Marked paid on the Desk, but the bill is still to be checked, so no payment "
                    "has been recorded. Confirm the bill and then register the payment."
                )
            )
        activity.bill_id = bill
        return bill
