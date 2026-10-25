# -*- coding: utf-8 -*-
"""Showcase data for Kenya eTIMS, in a company of its own.

Run once with `odoo shell` (see x_ke_etims/README.md, "Showcase data"). It
creates "Nairobi Traders Ltd (eTIMS demo)" with the Kenyan chart and a Demo
eTIMS device -- receipts are fake and marked DEMO, nothing reaches KRA -- and
fills it with one example of every feature. Other companies are not touched.
Running it again does nothing once the company exists.
"""

import base64
from datetime import timedelta

from odoo import Command, fields

NAME = "Nairobi Traders Ltd (eTIMS demo)"
installed = {m.name for m in env["ir.module.module"].search([("state", "=", "installed")])}

# One transaction: the queue's own commits are switched off, so a failure
# leaves nothing behind and the script can simply be run again.
Document = type(env["ke.etims.document"])
Document._ke_etims_can_commit = lambda self: False

if env["res.company"].search([("name", "=", NAME)]):
    print("%s already exists: nothing to do." % NAME)
else:
    ke, kes = env.ref("base.ke"), env.ref("base.KES")
    kes.active = True
    company = env["res.company"].create({
        "name": NAME, "country_id": ke.id, "currency_id": kes.id, "vat": "P051234567X",
        "street": "Moi Avenue 12", "city": "Nairobi", "phone": "+254 700 000 000",
        "email": "accounts@nairobitraders.example"})
    env["account.chart.template"].try_loading("ke", company=company, install_demo=False)
    if "stock.warehouse" in env and not env["stock.warehouse"].search(
            [("company_id", "=", company.id)]):
        env["stock.warehouse"].create({"name": "Nairobi (eTIMS demo)", "code": "NBO",
                                       "company_id": company.id})
    admins = env.ref("base.group_system").all_user_ids.filtered(lambda u: not u.share)
    admins.write({"company_ids": [Command.link(company.id)]})
    env = env(context=dict(env.context, allowed_company_ids=[company.id]))
    company = company.with_env(env)
    Code = env["ke.etims.code"]
    company.write({"ke_etims_default_backend": "demo", "ke_etims_environment": "demo",
                   "ke_etims_auto_send": True})
    device = env["ke.etims.device"].create({
        "company_id": company.id, "backend": "demo", "environment": "demo",
        "tin": "P051234567X", "bhf_id": "00", "serial": "DEMO-0001"})
    device.action_initialize()
    device.action_sync_codes()

    def tax(xml):
        return env.ref(env["account.chart.template"].company_xmlid(xml, company))
    st16, st0, pt16 = tax("ST16"), tax("ST0"), tax("PT16")
    item_class = env["ke.etims.item.class"].search([("code", "=", "5059690800")], limit=1)

    # A KRA item class set once on a category, taken by its products.
    grains = env["product.category"].create({"name": "Flour & Grains (eTIMS demo)",
                                             "ke_etims_item_class_id": item_class.id})
    gifts = env["product.category"].create({"name": "Gift Sets (eTIMS demo)"})

    def product(name, price, taxes, category, storable=True, cost=0.0, ptaxes=None):
        return env["product.product"].create({
            "name": name, "type": "consu", "is_storable": storable, "list_price": price,
            "standard_price": cost or price * 0.7, "categ_id": category.id,
            "company_id": company.id, "available_in_pos": True,
            "invoice_policy": "order",
            "taxes_id": [Command.set(taxes.ids)],
            "supplier_taxes_id": [Command.set((ptaxes if ptaxes is not None else pt16).ids)]})
    flour = product("Packaged maize flour 2kg", 1160.0, st16, grains, cost=800.0)
    oil = product("Cooking oil 1L", 450.0, st16, grains, cost=300.0)
    maize = product("Dry maize, 90kg bag", 4500.0, st0, grains, ptaxes=env["account.tax"])
    hamper = product("Gift hamper", 2500.0, st16, gifts, storable=False)

    def partner(name, vat=None, **values):
        return env["res.partner"].create(dict(
            {"name": name, "vat": vat, "country_id": ke.id, "is_company": True,
             "company_id": company.id}, **values))
    mombasa = partner("Mombasa Retail Ltd", "P059876543Y", city="Mombasa")
    kisumu = partner("Kisumu Supermarkets Ltd", "P052468135K", city="Kisumu")
    walk_in = partner("Walk-in customer", is_company=False)
    miller = partner("Unga Millers Ltd", "P000111222Z")
    cartons = partner("Kisumu Packaging Ltd", "P051111111A")
    bank = partner("Equity Bank Kenya", ke_etims_no_invoice_needed=True)

    def run_queue():
        Doc = env["ke.etims.document"].sudo()
        Doc.search([("state", "in", ("to_send", "pending"))]).write(
            {"next_try": fields.Datetime.now() - timedelta(seconds=1)})
        Doc._ke_etims_process_queue()

    today = fields.Date.context_today(env.user)

    def invoice(customer, lines, move_type="out_invoice", post=True, **values):
        move = env["account.move"].create(dict({
            "move_type": move_type, "partner_id": customer.id, "invoice_date": today,
            "company_id": company.id,
            "invoice_line_ids": [Command.create(line) for line in lines]}, **values))
        if post:
            move.action_post()
        return move

    def sold(p, qty, **extra):
        return dict({"product_id": p.id, "quantity": qty, "price_unit": p.list_price}, **extra)

    # Lines without a product (typed in, down payments) go as this item.
    env.ref("x_ke_etims.product_sundry").ke_etims_item_class_id = item_class

    # Signed invoices, one with a global discount folded into its lines.
    inv1 = invoice(mombasa, [sold(flour, 20), sold(oil, 12)])
    inv2 = invoice(kisumu, [sold(flour, 50)])
    invoice(walk_in, [sold(flour, 2), sold(oil, 2),
                      {"name": "Discount 10%", "quantity": 1, "price_unit": -321.0,
                       "tax_ids": [Command.set(st16.ids)]}], move_type="out_receipt")
    run_queue()
    # A credit note through the "Credit Note" button, with KRA's reason.
    wizard = env["account.move.reversal"].with_context(
        active_model="account.move", active_ids=inv2.ids).create({
            "journal_id": inv2.journal_id.id, "reason": "5 bags returned damaged",
            "ke_etims_reason_code_id": Code._ke_etims_find("32", "06").id})
    refund = env["account.move"].browse(wizard.refund_moves()["res_id"])
    refund.invoice_line_ids.quantity = 5
    refund.action_post()
    run_queue()
    # Failed: the gift hamper's category has no KRA item class.
    invoice(mombasa, [sold(hamper, 4)])
    run_queue()
    # Drafts that say, before posting, what eTIMS would refuse.
    invoice(kisumu, [sold(hamper, 1), sold(oil, 3)], post=False)
    invoice(mombasa, [sold(flour, 1)], move_type="out_refund", post=False)

    # Sales: an order with a 30% down payment, delivered, then the final
    # invoice deducting it -- the delivery waits for the sale to be signed.
    if "sale_stock" in installed:
        order = env["sale.order"].create({
            "partner_id": mombasa.id, "company_id": company.id,
            "order_line": [Command.create({"product_id": flour.id, "product_uom_qty": 30})]})
        order.action_confirm()
        env["sale.advance.payment.inv"].with_context(
            active_model="sale.order", active_ids=order.ids).create({
                "advance_payment_method": "percentage", "amount": 30}).create_invoices()
        order.invoice_ids.action_post()
        receipt_type = env["stock.picking.type"].search(
            [("code", "=", "incoming"), ("company_id", "=", company.id)], limit=1)
        supplier_location = env.ref("stock.stock_location_suppliers")
        receipt = env["stock.picking"].create({
            "picking_type_id": receipt_type.id, "partner_id": miller.id,
            "location_id": supplier_location.id,
            "location_dest_id": receipt_type.default_location_dest_id.id,
            "move_ids": [Command.create({
                "product_id": p.id, "product_uom_qty": q, "location_id": supplier_location.id,
                "location_dest_id": receipt_type.default_location_dest_id.id})
                for p, q in ((flour, 200), (oil, 120))]})
        receipt.action_confirm()
        for move in receipt.move_ids:
            move.write({"quantity": move.product_uom_qty, "picked": True})
        receipt.button_validate()
        delivery = order.picking_ids
        delivery.move_ids.write({"quantity": 30, "picked": True})
        delivery.button_validate()
        run_queue()
        final = order._create_invoices(final=True)
        final.action_post()
        run_queue()
        run_queue()

    # Supplier bills: with the supplier's eTIMS number, without one, and a
    # bank's charges, which need none.
    for vendor, ref, etims_ref, amount in ((cartons, "KP-2210", "KRACU0100000731/2210", 35.0),
                                           (cartons, "KP-2231", False, 35.0),
                                           (bank, "OCT-CHG", False, 4.0)):
        bill = env["account.move"].create({
            "move_type": "in_invoice", "partner_id": vendor.id, "invoice_date": today,
            "company_id": company.id, "ref": ref, "ke_etims_supplier_invoice_ref": etims_ref,
            "invoice_line_ids": [Command.create({
                "name": "Printed cartons" if vendor == cartons else "Bank charges",
                "quantity": 500, "price_unit": amount,
                "tax_ids": [Command.set(pt16.ids if vendor == cartons else [])]})]})
        bill.action_post()

    # What KRA holds for us: a supplier's eTIMS invoice and a customs import.
    if "x_ke_etims_stock" in installed:
        purchases = env["ke.etims.purchase"].sudo()._ke_etims_store(device, [{
            "supplier_pin": "P000111222Z", "supplier_name": "Unga Millers Ltd",
            "supplier_bhf_id": "00", "supplier_invoice_no": 4711,
            "supplier_cu_invoice": "KRACU0200000002/4711", "receipt_type": "P",
            "payment_type": "02", "sale_date": today, "total_taxable": 116000,
            "total_tax": 16000, "total_amount": 116000, "provider_ref": "P000111222Z/00/4711",
            "lines": [{"seq": 1, "item_code": "KE2NTBG0000009", "item_class_code": "5059690800",
                       "name": "Maize flour 2kg (Unga)", "quantity": 100, "unit_price": 1160,
                       "supply_amount": 116000, "discount_amount": 0, "tax_type": "B",
                       "taxable_amount": 116000, "tax_amount": 16000,
                       "total_amount": 116000}]}])
        purchases._ke_etims_create_bills()
        env["ke.etims.import"].sudo()._ke_etims_store(device, [{
            "provider_ref": "C0412-2026/1", "task_code": "C0412-2026",
            "name": "Sunflower oil, refined", "declaration_date": today,
            "declaration_no": "26MSA0412IM", "item_seq": 1, "hs_code": "1512191000",
            "origin_country": "MY", "export_country": "MY", "quantity": 2400,
            "quantity_unit": "U", "package_quantity": 200, "packaging_unit": "CT",
            "supplier_name": "Selangor Oils Sdn Bhd", "invoice_amount": 9600,
            "invoice_currency": "USD", "exchange_rate": 129.4, "status": "2"}])

    if "x_ke_etims_mrp" in installed:
        env["mrp.bom"].create({
            "product_tmpl_id": flour.product_tmpl_id.id, "product_qty": 45,
            "company_id": company.id,
            "bom_line_ids": [Command.create({"product_id": maize.id, "product_qty": 1})]})

    # VAT: this month's return, and KRA's iTax files matched on eTIMS numbers.
    if "x_ke_etims_vat" in installed:
        vat_return = env["ke.vat.return"].create({
            "company_id": company.id, "report_id": env.ref("l10n_ke.tax_report_ke").id,
            "date_from": today.replace(day=1),
            "date_to": (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            - timedelta(days=1)})
        header = "Invoice No,Invoice Date,PIN of Purchaser,Taxable Value,Amount of VAT"
        day = today.strftime("%d/%m/%Y")
        for section, rows in (
                ("sales_pin", [
                    "%s,%s,%s,%s,%s" % (inv1.ke_etims_cu_invoice, day, mombasa.vat,
                                        inv1.amount_untaxed, inv1.amount_tax),
                    "%s,%s,%s,%s,%s" % (inv2.ke_etims_document_id.kra_invoice_no, day,
                                        kisumu.vat, inv2.amount_untaxed, inv2.amount_tax - 80),
                    "KRACU0100009999/88,%s,P059876543Y,10000,1600" % day]),
                ("purchases_local", [
                    "KRACU0100000731/2210,%s,P051111111A,17500,2800" % day])):
            env["ke.vat.itax.batch"].create({
                "return_id": vat_return.id, "section": section,
                "csv_file": base64.b64encode("\n".join([header] + rows).encode()),
                "csv_filename": "%s.csv" % section}).action_import()

    # Point of Sale: a shop with cash and M-Pesa, each with its KRA type.
    if "x_ke_etims_pos" in installed:
        Journal = env["account.journal"]
        cash_journal = Journal.search([("type", "=", "cash"), ("company_id", "=", company.id)],
                                      limit=1) or Journal.create({
            "name": "Cash", "type": "cash", "code": "CSH", "company_id": company.id})
        cash = env["pos.payment.method"].create({
            "name": "Cash", "company_id": company.id, "journal_id": cash_journal.id})
        mpesa = env["pos.payment.method"].create({
            "name": "M-Pesa", "company_id": company.id, "journal_id": Journal.search(
                [("type", "=", "bank"), ("company_id", "=", company.id)], limit=1).id})
        env["pos.config"].create({
            "name": "Moi Avenue Shop (eTIMS demo)", "company_id": company.id,
            "ke_etims_enabled": True,
            "payment_method_ids": [Command.set((cash | mpesa).ids)]})

    run_queue()  # stock levels that followed the last movements
    env.cr.commit()
    print("Created %s. Switch to it from the company menu (top right)." % NAME)
