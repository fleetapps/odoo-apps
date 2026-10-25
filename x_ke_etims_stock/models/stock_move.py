# -*- coding: utf-8 -*-
"""Done stock moves, reported to eTIMS as stock movements.

Which KRA movement type (code class 12) a move is follows from where it went:

========================  ====  =========================================
from -> to                code  KRA's name
========================  ====  =========================================
vendor -> stock           02    Purchase
customer -> stock         03    Return
transit -> stock          04    Stock movement (in from another branch)
production -> stock       05    Processing (manufactured)
inventory -> stock        06    Adjustment
stock -> customer         11    Sale
stock -> vendor           12    Return
stock -> transit          13    Stock movement (out to another branch)
stock -> production       14    Processing (consumed)
stock -> scrap            15    Discarding
stock -> inventory        16    Adjustment
========================  ====  =========================================

A move between two locations of the same branch is not a movement KRA
counts, and is not reported. Only storable products are.

A picking is one stock movement when all its moves are of one type; anything
else -- a mixed picking, an adjustment, a scrap, a production move -- is
reported move by move. After a movement is accepted the stock master of each
of its products follows, with the quantity on hand: KRA wants the level after
the movement, not before.

A delivery for a sale waits until the sale's invoice (or its POS order) is
signed. KRA files a sale before the stock it moved and refuses the other order.
"""

from decimal import Decimal

from odoo import _, fields, models

from odoo.addons.x_ke_etims.backends import Line, StockIO
from odoo.addons.x_ke_etims.backends.base import money

INCOMING = {"supplier": "02", "customer": "03", "transit": "04", "production": "05",
            "inventory": "06"}
OUTGOING = {"customer": "11", "supplier": "12", "transit": "13", "production": "14",
            "inventory": "16"}
SCRAP = "15"


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, cancel_backorder=False):
        moves = super()._action_done(cancel_backorder=cancel_backorder)
        moves._ke_etims_enqueue_stock()
        return moves

    def _ke_etims_io_type(self):
        """KRA's movement type for this move, or False if KRA does not count it."""
        self.ensure_one()
        source, destination = self.location_id.usage, self.location_dest_id.usage
        if destination == "internal" and source != "internal":
            return INCOMING.get(source, False)
        if source == "internal" and destination != "internal":
            if destination == "inventory" and self.scrap_id:
                return SCRAP
            return OUTGOING.get(destination, False)
        return False

    def _ke_etims_reportable(self):
        return self.filtered(
            lambda m: m.state == "done" and m.product_id.is_storable and m.quantity
            and m.company_id._ke_etims_enabled() and m._ke_etims_io_type())

    def _ke_etims_enqueue_stock(self):
        Document = self.env["ke.etims.document"].sudo()
        moves = self._ke_etims_reportable()
        for picking in moves.picking_id:
            picking_moves = moves.filtered(lambda m, p=picking: m.picking_id == p)
            if len({move._ke_etims_io_type() for move in picking_moves}) == 1:
                Document._ke_etims_enqueue(picking, "stock_io")
            else:
                for move in picking_moves:
                    Document._ke_etims_enqueue(move, "stock_io")
        for move in moves.filtered(lambda m: not m.picking_id):
            Document._ke_etims_enqueue(move, "stock_io")

    # ------------------------------------------------- shared with pickings

    def _ke_etims_unit_price(self):
        """The unit price the movement is valued at, VAT excluded: what the
        order says, where there is one, else the product's cost."""
        self.ensure_one()
        if "purchase_line_id" in self._fields and self.purchase_line_id:
            return self.purchase_line_id.price_unit
        if "sale_line_id" in self._fields and self.sale_line_id:
            return self.sale_line_id.price_unit
        return self.product_id.with_company(self.company_id).standard_price

    def _ke_etims_sales_to_wait_for(self):
        """The eTIMS documents of the sales these moves deliver: an invoice
        for a sale order line, a POS order. Empty when the moves deliver no
        sale, or None when a sale has not been invoiced yet."""
        documents = self.env["ke.etims.document"]
        for move in self:
            sale_line = move.sale_line_id if "sale_line_id" in move._fields else None
            if sale_line:
                invoices = sale_line.invoice_lines.move_id.filtered(
                    lambda m: m.state == "posted" and m.move_type in ("out_invoice", "out_receipt"))
                if not invoices:
                    return None
                documents |= invoices.sudo().ke_etims_document_id
            picking = move.picking_id
            if picking and "pos_order_id" in picking._fields and picking.pos_order_id:
                documents |= picking.pos_order_id.sudo().ke_etims_document_id
        return documents

    def _ke_etims_stock_wait_reason(self):
        if self[:1]._ke_etims_io_type() != OUTGOING["customer"]:
            return False
        sales = self._ke_etims_sales_to_wait_for()
        if sales is None:
            return _("Waiting for the sale it delivers to be invoiced and signed.")
        unsigned = sales.filtered(lambda d: d.state != "sent")
        if unsigned:
            return _("Waiting for %s to be signed by eTIMS.",
                     ", ".join(unsigned.mapped("display_name")))
        return False

    def _ke_etims_stock_validate(self, document):
        errors = []
        device = document.device_id
        for move in self:
            template = move.product_id.product_tmpl_id
            if not template._ke_etims_registration(device) \
                    and not template._ke_etims_register(device, document=document,
                                                        raise_errors=False):
                errors.append(_("%(product)s is not registered with eTIMS: %(reason)s",
                                product=template.display_name,
                                reason=", ".join(template._ke_etims_missing())
                                or _("no sales tax with a KRA tax type")))
        return errors

    def _ke_etims_stock_io(self, document, reference, partner):
        device = document.device_id
        if not document.kra_sequence_no:
            document.kra_sequence_no = device._ke_etims_next_sar_no()
        rates = device._ke_etims_tax_rates()
        lines = []
        for seq, move in enumerate(self, start=1):
            product = move.product_id
            template = product.product_tmpl_id
            registration = template._ke_etims_registration(device)
            tax_type = template._ke_etims_tax_type(device.company_id)
            rate = Decimal(str(rates.get(tax_type, 0) or 0))
            quantity = Decimal(str(move.product_uom._compute_quantity(
                move.quantity, product.uom_id)))
            unit = money(Decimal(str(move._ke_etims_unit_price())) * (100 + rate) / 100)
            supply = money(quantity * unit)
            lines.append(Line(
                seq=seq,
                item_code=registration.item_code or template.ke_etims_item_code,
                item_class_code=template.ke_etims_item_class_id.code,
                name=template.name,
                quantity=money(quantity),
                unit_price=unit,
                tax_type=tax_type,
                supply_amount=supply,
                discount_amount=Decimal("0.00"),
                taxable_amount=supply,
                tax_amount=money(supply * rate / (100 + rate)) if rate else Decimal("0.00"),
                packaging_unit=template.ke_etims_packaging_unit_id.code,
                quantity_unit=template.ke_etims_quantity_unit_id.code,
                package_quantity=money(quantity),
                barcode=product.barcode or None,
                provider_item_id=registration.provider_item_id or None,
            ))
        io_type = self[0]._ke_etims_io_type()
        commercial = partner.commercial_partner_id if partner else partner
        # The counterpart of the movement: supplier, customer or other branch.
        with_partner = io_type in ("02", "03", "04", "11", "12", "13")
        return StockIO(
            sar_no=document.kra_sequence_no,
            io_type=io_type,
            lines=lines,
            occurred_on=fields.Date.context_today(self, self[0].date),
            operator=device._ke_etims_operator(),
            customer_pin=(commercial.vat or None) if with_partner and commercial else None,
            customer_name=(commercial.name or None) if with_partner and commercial else None,
            customer_bhf_id=(commercial.ke_etims_branch_code or None)
            if with_partner and commercial else None,
            remark=reference,
        )

    def _ke_etims_stock_signed(self, document):
        """Movement accepted: now each product's level on this device."""
        self.env["product.product"]._ke_etims_enqueue_stock_master(
            document.device_id, self.product_id)

    # ---------------------------------------- the source API, for one move

    def _ke_etims_device(self):
        self.ensure_one()
        return self.company_id._ke_etims_device()

    def _ke_etims_wait_reason(self, document):
        return self._ke_etims_stock_wait_reason()

    def _ke_etims_validate(self, document):
        return self._ke_etims_stock_validate(document)

    def _ke_etims_prepare(self, document):
        return self._ke_etims_stock_io(document, self.reference or self.display_name,
                                       self.partner_id or self.picking_id.partner_id)

    def _ke_etims_on_signed(self, document):
        self._ke_etims_stock_signed(document)

    def _ke_etims_on_failed(self, document):
        document._ke_etims_schedule_activity()


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _ke_etims_moves(self):
        self.ensure_one()
        return self.move_ids._ke_etims_reportable()

    def _ke_etims_device(self):
        self.ensure_one()
        return self.company_id._ke_etims_device()

    def _ke_etims_wait_reason(self, document):
        return self._ke_etims_moves()._ke_etims_stock_wait_reason()

    def _ke_etims_validate(self, document):
        moves = self._ke_etims_moves()
        if not moves:
            return [_("%s has no stock movement KRA counts.", self.display_name)]
        return moves._ke_etims_stock_validate(document)

    def _ke_etims_prepare(self, document):
        return self._ke_etims_moves()._ke_etims_stock_io(document, self.name, self.partner_id)

    def _ke_etims_on_signed(self, document):
        self.message_post(body=_("Reported to eTIMS as stock movement %s.",
                                 document.kra_sequence_no))
        self._ke_etims_moves()._ke_etims_stock_signed(document)

    def _ke_etims_on_failed(self, document):
        self.message_post(body=_("eTIMS refused the stock movement: %s", document.last_error))
        document._ke_etims_schedule_activity()
