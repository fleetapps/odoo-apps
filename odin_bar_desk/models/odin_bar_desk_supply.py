"""Bar Desk: asking for stock, ordering from suppliers, paying on delivery.

Same rules as the rest of the Desk (see odin_bar_desk.py): every call names
the bar and carries the staff token; stock actions carry a request id and are
posted once.
"""

import logging
import math
import re
from datetime import timedelta
from urllib.parse import quote

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from .odin_bar_desk import LIST_LIMIT, _short_uom

_logger = logging.getLogger(__name__)

# Asks and answers stay on the asking bar's screen this long.
REQUEST_HOURS = 16
# A store count this recent is where the order suggestion starts from.
ORDER_COUNT_HOURS = 16


class OdinBarDesk(models.AbstractModel):
    _inherit = "odin.bar.desk"

    # ------------------------------------------------------------------
    # Ask for stock
    # ------------------------------------------------------------------

    @api.model
    def _desk_request_sources(self, bar):
        """Where a bar can ask for stock: its store first, then the other bars."""
        Bar = self.env["odin.bar"].sudo()
        company = [("company_id", "=", bar.company_id.id)]
        store = bar.store_id or Bar.search(company + [("kind", "=", "store")], limit=1)
        return store | Bar.search(company + [("kind", "=", "bar"), ("id", "!=", bar.id)])

    @api.model
    def _desk_request_record(self, request_id):
        request = self.env["odin.bar.request"].sudo().browse(int(request_id or 0)).exists()
        if not request:
            raise UserError(_("This request no longer exists."))
        return request

    @api.model
    def _desk_request_info(self, request):
        chain, asked = request, request.source_bar_id
        while chain.passed_from_id:
            chain = chain.passed_from_id
            asked |= chain.source_bar_id
        answered = request.state in ("sent", "partial", "none")
        ask_next = self.env["odin.bar"]
        if answered and request.missing_count and not request.passed_to_ids:
            ask_next = self._desk_request_sources(request.bar_id) - asked
        return {
            "id": request.id,
            "bar": request.bar_id.name,
            "source": request.source_bar_id.name,
            "state": request.state,
            "time": self._desk_time_label(request.bar_id, request.date),
            "who": request.employee_id.name or "",
            "answered_by": request.answered_by_id.name or "",
            "note": request.note or "",
            "lines": [
                {
                    "id": line.id,
                    "product_id": line.product_id.id,
                    "name": line.product_id.display_name,
                    "uom_id": line.uom_id.id,
                    "uom_name": _short_uom(line.uom_id),
                    "pack": line.uom_id != line.product_id.uom_id,
                    "qty": line.qty,
                    "sent_qty": line.sent_qty,
                    "missing_qty": line.missing_qty,
                }
                for line in request.line_ids
            ],
            "ask_next": [{"id": other.id, "name": other.name} for other in ask_next],
        }

    @api.model
    def desk_requests(self, bar_id, token):
        """Asks waiting for this location to answer, and this bar's own asks
        of the last hours."""
        bar, _employee = self._desk_context(bar_id, token)
        Request = self.env["odin.bar.request"].sudo()
        incoming = Request.search(
            [("source_bar_id", "=", bar.id), ("state", "=", "open")], order="date", limit=LIST_LIMIT
        )
        mine = Request.search(
            [
                ("bar_id", "=", bar.id),
                ("state", "!=", "cancel"),
                "|",
                ("state", "=", "open"),
                ("date", ">=", fields.Datetime.now() - timedelta(hours=REQUEST_HOURS)),
            ],
            limit=LIST_LIMIT,
        )
        return {
            "incoming": [self._desk_request_info(request) for request in incoming],
            "mine": [self._desk_request_info(request) for request in mine],
            "sources": [
                {"id": source.id, "name": source.name, "kind": source.kind}
                for source in (self._desk_request_sources(bar) if bar.kind == "bar" else [])
            ],
        }

    @api.model
    def _desk_request_create(self, bar, employee, uuid, source_bar_id, items, passed_from=None, note=False):
        source = self.env["odin.bar"].sudo().browse(int(source_bar_id or 0)).exists()
        if bar.kind != "bar":
            raise UserError(_("Bars ask for stock; the store sends it."))
        if source not in self._desk_request_sources(bar):
            raise UserError(_("Pick where to ask."))
        request = self.env["odin.bar.request"].sudo().create(
            {
                "bar_id": bar.id,
                "source_bar_id": source.id,
                "employee_id": employee.id,
                "business_date": bar._business_date(),
                "note": (note or "").strip() or False,
                "passed_from_id": passed_from.id if passed_from else False,
                "line_ids": [
                    Command.create({"product_id": product.id, "uom_id": uom.id, "qty": qty})
                    for product, uom, qty in items
                ],
            }
        )
        activity = self._desk_begin(
            uuid,
            {
                "kind": "ask",
                "bar_id": bar.id,
                "dest_bar_id": source.id,
                "employee_id": employee.id,
                "business_date": bar._business_date(),
                "request_id": request.id,
                "summary": self._desk_summary(items),
            },
        )
        return self._desk_result(activity)

    @api.model
    def desk_request_create(self, bar_id, token, uuid, source_bar_id, lines, note=False):
        """Ask the store or another bar for stock. Nothing moves until they send it."""
        bar, employee = self._desk_context(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        return self._desk_request_create(
            bar, employee, uuid, source_bar_id, self._desk_items(bar, lines), note=note
        )

    @api.model
    def desk_request_pass_on(self, bar_id, token, uuid, request_id, source_bar_id):
        """Ask another location for what an answered request did not bring."""
        bar, employee = self._desk_context(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        request = self._desk_request_record(request_id)
        if request.bar_id != bar:
            raise UserError(_("This request was not made by %(bar)s.", bar=bar.name))
        if request.passed_to_ids:
            raise UserError(_("This request was already passed on."))
        missing = request.line_ids.filtered(lambda line: line.missing_qty > 0)
        if not missing:
            raise UserError(_("Everything asked for was sent."))
        items = [(line.product_id, line.uom_id, line.missing_qty) for line in missing]
        return self._desk_request_create(
            bar, employee, uuid, source_bar_id, items, passed_from=request, note=request.note
        )

    @api.model
    def desk_request_cancel(self, bar_id, token, request_id):
        bar, _employee = self._desk_context(bar_id, token)
        request = self._desk_request_record(request_id)
        if request.bar_id != bar:
            raise UserError(_("This request was not made by %(bar)s.", bar=bar.name))
        if request.state == "open":
            request.state = "cancel"
        return True

    @api.model
    def _desk_transfer_type(self, source, dest):
        """Operation type for stock going from ``source`` to bar ``dest``."""
        if source.kind == "store":
            picking_type = dest.issue_type_id
            if not picking_type:
                raise UserError(_("Set the issue type on %(bar)s first.", bar=dest.name))
            return picking_type
        reason = self.env["odin.bar.reason"].sudo().search(
            [("company_id", "=", source.company_id.id), ("operation", "=", "transfer")], limit=1
        )
        if not reason.picking_type_id:
            raise UserError(_("Set the operation type of the \"To another bar\" stock-out reason first."))
        return reason.picking_type_id

    @api.model
    def desk_request_answer(self, bar_id, token, uuid, request_id, sent):
        """Send what this location has of a request (``sent``: {line id:
        quantity in the line's unit}), validated at once; zero means not
        available. It shows at the asking bar in Stock in."""
        source, employee = self._desk_context(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        request = self._desk_request_record(request_id)
        if request.source_bar_id != source:
            raise UserError(_("This request was not made to %(bar)s.", bar=source.name))
        if request.state != "open":
            raise UserError(_("This request was already answered or cancelled."))
        quantities = {int(line_id): float(qty or 0.0) for line_id, qty in (sent or {}).items()}
        if any(qty < 0 for qty in quantities.values()) or set(quantities) - set(request.line_ids.ids):
            raise UserError(_("These quantities do not match the request."))
        for line in request.line_ids:
            line.sent_qty = line.uom_id.round(quantities.get(line.id, 0.0))
        items = [
            (line.product_id, line.uom_id, line.sent_qty)
            for line in request.line_ids
            if not line.uom_id.is_zero(line.sent_qty)
        ]
        missing = request.line_ids.filtered(lambda line: line.qty - line.sent_qty > 1e-6)
        dest = request.bar_id
        activity = self._desk_begin(
            uuid,
            {
                "kind": "send" if items else "ask_none",
                "bar_id": source.id,
                "dest_bar_id": dest.id,
                "employee_id": employee.id,
                "business_date": source._business_date(),
                "request_id": request.id,
                "summary": self._desk_summary(items) if items else ", ".join(
                    missing.product_id.mapped("display_name")
                ),
                "amount": self._desk_value(source, items) if items else 0.0,
            },
        )
        picking = self.env["stock.picking"]
        if items:
            picking = self._desk_picking(
                source,
                self._desk_transfer_type(source, dest),
                source.location_id,
                dest.location_id,
                items,
                {
                    "bar_employee_id": employee.id,
                    "bar_activity_id": activity.id,
                    "origin": _("Asked by %(bar)s", bar=dest.name),
                },
            )
        request.write(
            {
                "state": "none" if not items else "partial" if missing else "sent",
                "answered_by_id": employee.id,
                "answered_at": fields.Datetime.now(),
                "picking_id": picking.id,
            }
        )
        return self._desk_result(activity)

    # ------------------------------------------------------------------
    # Order from suppliers
    # ------------------------------------------------------------------

    @api.model
    def _desk_order_context(self, bar_id, token):
        store, employee = self._desk_store(bar_id, token)
        if not (employee.sudo().odin_bar_can_order or self._desk_is_manager()):
            raise UserError(_("Only staff allowed to order from suppliers can do this."))
        return store, employee

    @api.model
    def _desk_supplier(self, product, company):
        sellers = product.seller_ids.filtered(
            lambda seller: seller.company_id in (company, self.env["res.company"])
            and (not seller.product_id or seller.product_id == product)
        )
        return sellers[:1].partner_id

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
    def _desk_store_available(self, store, products):
        """Stock at the store now, in each product's unit. A product counted in
        the last hours starts from what was counted, plus what moved since, so
        the suggestion is right before the count is approved."""
        result = {}
        lines = self.env["odin.bar.count.line"].sudo().search(
            [
                ("bar_id", "=", store.id),
                ("product_id", "in", products.ids),
                ("touched", "=", True),
                ("count_id.state", "in", ("submitted", "recount", "approved")),
                ("count_id.submitted_at", ">=", fields.Datetime.now() - timedelta(hours=ORDER_COUNT_HOURS)),
            ],
            order="id desc",
        )
        now = fields.Datetime.now()
        day = store._business_date()
        for line in lines:
            product = line.product_id
            if product.id in result:
                continue
            since = line.count_id.submitted_at
            moved = store._stock_as_of(product, now, day)[product.id] - store._stock_as_of(product, since, day)[product.id]
            result[product.id] = line.counted_qty + moved
        rest = products.filtered(lambda product: product.id not in result)
        if rest:
            quants = self.env["stock.quant"].sudo()._read_group(
                [("location_id", "child_of", store.location_id.id), ("product_id", "in", rest.ids)],
                ["product_id"],
                ["quantity:sum"],
            )
            on_hand = {product.id: qty for product, qty in quants}
            for product in rest:
                result[product.id] = on_hand.get(product.id, 0.0)
        return result

    @api.model
    def _desk_store_incoming(self, store, products):
        """Quantity already ordered and not yet delivered, in each product's unit."""
        moves = self.env["stock.move"].sudo()._read_group(
            [
                ("product_id", "in", products.ids),
                ("location_dest_id", "child_of", store.location_id.id),
                ("location_id.usage", "=", "supplier"),
                ("state", "not in", ("done", "cancel", "draft")),
            ],
            ["product_id"],
            ["product_qty:sum"],
        )
        return {product.id: qty for product, qty in moves}

    @api.model
    def _desk_order_partner_info(self, partner, store):
        return {
            "id": partner.id,
            "name": partner.display_name,
            "email": partner.email or "",
            "phone": partner.phone or "",
        }

    @api.model
    def desk_order_suggestions(self, bar_id, token):
        """What to order, per supplier: the usual level at the store less what
        is there and what is already on order, in the order unit."""
        store, _employee = self._desk_order_context(bar_id, token)
        company = store.company_id
        products = store._desk_products().filtered(lambda product: product.bar_usual_qty > 0)
        available = self._desk_store_available(store, products)
        incoming = self._desk_store_incoming(store, products)
        by_supplier = {}
        no_supplier = []
        for product in products:
            order_uom = product.bar_order_uom_id or product.uom_id
            factor = order_uom._compute_quantity(1, product.uom_id, round=False) or 1.0
            need = product.bar_usual_qty * factor - available.get(product.id, 0.0) - incoming.get(product.id, 0.0)
            if need <= 1e-6:
                continue
            qty = math.ceil(need / factor - 1e-6)
            supplier = self._desk_supplier(product, company)
            if not supplier:
                no_supplier.append(product.display_name)
                continue
            by_supplier.setdefault(supplier, []).append(
                {"product_id": product.id, "uom_id": order_uom.id, "qty": qty}
            )
        start = fields.Datetime.now() - timedelta(hours=REQUEST_HOURS)
        orders = self.env["odin.bar.activity"].sudo().search(
            [("bar_id", "=", store.id), ("kind", "=", "order"), ("date", ">=", start)]
        )
        ordered = {}
        for activity in orders:
            ordered.setdefault(activity.partner_id, []).append(
                {
                    "name": activity.purchase_id.name,
                    "time": self._desk_time_label(store, activity.date),
                    "who": activity.employee_id.name or "",
                    "summary": activity.summary or "",
                }
            )
        suppliers = []
        for partner in sorted(set(by_supplier) | set(ordered), key=lambda p: p.display_name or ""):
            suppliers.append(
                dict(
                    self._desk_order_partner_info(partner, store),
                    lines=by_supplier.get(partner, []),
                    ordered=ordered.get(partner, []),
                )
            )
        return {"suppliers": suppliers, "no_supplier": sorted(no_supplier)}

    @api.model
    def _desk_whatsapp(self, partner, company, text):
        digits = re.sub(r"\D", "", partner.phone or "")
        if digits.startswith("0") and company.country_id.phone_code:
            digits = f"{company.country_id.phone_code}{digits[1:]}"
        return f"https://wa.me/{digits}?text={quote(text)}"

    @api.model
    def _desk_order_info(self, order, store):
        lines = [
            f"{line.product_qty:g} × {_short_uom(line.product_uom_id)} {line.product_id.display_name}"
            for line in order.order_line
            if not line.display_type
        ]
        text = "\n".join(
            [
                _("Order %(order)s from %(company)s", order=order.name, company=order.company_id.name),
                *lines,
                _("Please deliver to %(store)s. Thank you.", store=store.name),
            ]
        )
        return {
            "order": order.name,
            "supplier": order.partner_id.display_name,
            "emailed": bool(
                self.env["odin.bar.activity"].sudo().search_count(
                    [("purchase_id", "=", order.id), ("note", "!=", False)], limit=1
                )
            ),
            "whatsapp": self._desk_whatsapp(order.partner_id, order.company_id, text),
            "text": text,
        }

    @api.model
    def desk_order_send(self, bar_id, token, uuid, partner_id, lines):
        """Place an order with a supplier: a confirmed purchase order delivering
        to the store, emailed to the supplier when they have an email."""
        store, employee = self._desk_order_context(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        partner = self.env["res.partner"].sudo().browse(int(partner_id or 0)).exists()
        if not partner or partner.company_id not in (store.company_id, self.env["res.company"]):
            raise UserError(_("Pick the supplier."))
        if not store.receipt_type_id:
            raise UserError(_("Set the receipt type on %(store)s first.", store=store.name))
        items = self._desk_items(store, lines)
        order = (
            self.env["purchase.order"]
            .sudo()
            .with_company(store.company_id)
            .create(
                {
                    "partner_id": partner.id,
                    "company_id": store.company_id.id,
                    "picking_type_id": store.receipt_type_id.id,
                    "order_line": [
                        Command.create(
                            {
                                "product_id": product.id,
                                "product_qty": qty,
                                "product_uom_id": uom.id,
                                "price_unit": self._desk_supplier_price(product.with_company(store.company_id), partner, uom, qty),
                            }
                        )
                        for product, uom, qty in items
                    ],
                }
            )
        )
        order.button_confirm()
        activity = self._desk_begin(
            uuid,
            {
                "kind": "order",
                "bar_id": store.id,
                "partner_id": partner.id,
                "employee_id": employee.id,
                "business_date": store._business_date(),
                "purchase_id": order.id,
                "summary": self._desk_summary(items),
                "amount": order.amount_untaxed,
            },
        )
        template = self.env.ref("purchase.email_template_edi_purchase_done", raise_if_not_found=False)
        if partner.email and template:
            # An email that cannot go (no PDF engine, no mail server) never
            # blocks the order: the Desk then offers WhatsApp.
            try:
                with self.env.cr.savepoint():
                    template.sudo().send_mail(order.id)
                    activity.note = _("Emailed to %(email)s", email=partner.email)
            except Exception:
                _logger.exception("Bar Desk: order %s could not be emailed", order.name)
        return self._desk_result(activity)

    @api.model
    def _desk_result(self, activity):
        result = super()._desk_result(activity)
        if activity.purchase_id:
            result["order"] = self._desk_order_info(activity.purchase_id, activity.bar_id)
        if activity.bill_id:
            result["bill"] = {
                "name": activity.bill_id.name,
                "paid": activity.bill_id.payment_state in ("paid", "in_payment"),
            }
        return result

    # ------------------------------------------------------------------
    # Paying on a supplier delivery
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
    def _desk_supplier_bill(self, store, partner, lines, paid, supplier_ref, activity, origin=False):
        """Bill what arrived (``lines``: [(product, uom, qty, purchase line or
        empty)]) and, when paid now, pay it from the store's account."""
        company = store.company_id
        today = fields.Date.context_today(self.with_context(tz=store.tz))
        invoice_lines = []
        for product, uom, qty, purchase_line in lines:
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
                    "move_type": "in_invoice",
                    "partner_id": partner.id,
                    "company_id": company.id,
                    "invoice_date": today,
                    "ref": (supplier_ref or "").strip() or False,
                    "invoice_origin": origin or False,
                    "invoice_line_ids": invoice_lines,
                }
            )
        )
        bill.action_post()
        if paid == "now":
            self.env["account.payment.register"].sudo().with_company(company).with_context(
                active_model="account.move", active_ids=bill.ids
            ).create(
                {"journal_id": store.supplier_payment_journal_id.id, "payment_date": today}
            )._create_payments()
        activity.bill_id = bill
        return bill
