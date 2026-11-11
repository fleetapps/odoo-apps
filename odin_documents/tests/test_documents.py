import re

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestDocuments(AccountTestInvoicingCommon):
    """Every document renders from the record it belongs to, on the company's
    letterhead with its terms, and shows only where it applies."""

    @classmethod
    @AccountTestInvoicingCommon.setup_country("ke")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # The invoicing test user also quotes, keeps HR records and raises requisitions here.
        cls.env.user.group_ids += (cls.env.ref("sales_team.group_sale_manager") | cls.env.ref("hr.group_hr_manager")
                                   | cls.env.ref("purchase_request.group_purchase_request_manager"))
        cls.company.write({
            "name": "Rinz Products Limited", "doc_short_name": "RINZ", "vat": "P052112729P",
            "street": "Warehouse 4, Road C", "city": "Nairobi", "zip": "P.O. Box 43117-00100",
            "phone": "+254 720 786 679", "email": "info@example.com",
            "doc_mpesa_paybill": "982800", "doc_mpesa_account": "110110", "doc_brand": "RINZ",
        })
        cls.usd = cls.setup_other_currency("USD", rates=[("2026-01-01", 1 / 125.0)])
        bank = cls.env["res.bank"].create({"name": "Prime Bank Limited", "bic": "PRIEKENX", "street": "Riverside Drive"})
        Account = cls.env["res.partner.bank"]
        # Trusted ("Send Money"), as Odoo 19 requires before an invoice naming them is posted.
        Account.create({"acc_number": "3000184558", "partner_id": cls.company.partner_id.id, "bank_id": bank.id,
                        "currency_id": cls.company.currency_id.id, "allow_out_payment": True})
        Account.create({"acc_number": "3001232272", "partner_id": cls.company.partner_id.id, "bank_id": bank.id,
                        "currency_id": cls.usd.id, "allow_out_payment": True})
        kenya, rwanda = cls.env.ref("base.ke"), cls.env.ref("base.rw")
        cls.partner_a.write({"country_id": kenya.id, "city": "Nairobi", "vat": "P000000001A"})
        cls.r4 = cls.env["res.partner"].create({
            "name": "R4 Distributors Limited", "is_company": True, "country_id": rwanda.id, "city": "Kigali",
            "street": "Kubaho Plaza", "vat": "157041913", "ref": "EXP/03/2022"})
        cls.product_a.write({
            "name": "Rich Pearl Handwash 500 Ml", "default_code": "FIN-PAK-095", "is_storable": True,
            "hs_code": "34013000", "country_of_origin": kenya.id, "doc_kebs_permit": "SM#56791",
            "doc_pack_size": "0.5 L", "doc_net_weight": 0.5, "weight": 0.56, "doc_carton_qty": 12})
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.Report = cls.env["ir.actions.report"]

    # ------------------------------------------------------------ helpers
    def render(self, xmlid, records):
        html, _kind = self.Report._render_qweb_html(f"odin_documents.{xmlid}", records.ids)
        return html.decode()

    def report(self, xmlid):
        return self.env.ref(f"odin_documents.{xmlid}")

    def invoice(self, partner, lines, move_type="out_invoice", currency=None, post=True):
        move = self.env["account.move"].create({
            "move_type": move_type, "partner_id": partner.id, "invoice_date": "2026-11-02",
            "currency_id": (currency or self.company.currency_id).id,
            "invoice_line_ids": [Command.create(vals) for vals in lines]})
        if post:
            move.action_post()
        return move

    def local_invoice(self, **kwargs):
        return self.invoice(self.partner_a, [{"product_id": self.product_a.id, "quantity": 10, "price_unit": 199.0}],
                            **kwargs)

    def export_invoice(self, currency=None, post=False):
        return self.invoice(self.r4, [
            {"product_id": self.product_a.id, "quantity": 120, "price_unit": 130.0, "tax_ids": [Command.clear()]},
            {"name": "Export packaging and EAC certificate", "quantity": 1, "price_unit": 34900.0,
             "tax_ids": [Command.clear()]},
        ], currency=currency, post=post)

    def picking(self, picking_type, partner, qty=24, **vals):
        return self.env["stock.picking"].create({
            "picking_type_id": picking_type.id, "partner_id": partner.id if partner else False,
            "location_id": picking_type.default_location_src_id.id,
            "location_dest_id": picking_type.default_location_dest_id.id, **vals,
            "move_ids": [Command.create({
                "product_id": self.product_a.id, "product_uom_qty": qty, "product_uom": self.product_a.uom_id.id,
                "location_id": picking_type.default_location_src_id.id,
                "location_dest_id": picking_type.default_location_dest_id.id})],
        })

    # ------------------------------------------------------------ the app
    def test_seventeen_documents_each_with_its_report_and_records(self):
        types = self.env["odin.document.type"].search([])
        self.assertEqual(len(types), 17)
        self.assertFalse(types.filtered(lambda t: not t.report_id), "every document prints with its own report")
        self.assertEqual(set(types.mapped("area")), {"sales", "export", "purchases", "stock", "internal"})
        self.local_invoice()
        invoices = types.filtered(lambda t: t.code == "sales_invoice")
        self.assertGreaterEqual(invoices.record_count, 1)
        for doc in types:
            action = doc.action_open_records()
            self.assertIn(action["type"], ("ir.actions.act_window", "ir.actions.server"), doc.name)

    def test_terms_print_with_the_company_name(self):
        terms = self.env["odin.document.type"]._get("sales_invoice")._terms_for(self.company)
        self.assertIn("Title to goods remains with <strong>Rinz Products Limited</strong>", str(terms))
        self.assertNotIn("{company}", str(terms))

    # ------------------------------------------------------------ print menus
    def test_print_menus_offer_the_companys_documents_not_odoos(self):
        reports = {a["id"] for a in self.env["ir.actions.actions"].get_bindings("account.move").get("report", [])}
        self.assertIn(self.report("action_report_sales_invoice").id, reports)
        self.assertNotIn(self.env.ref("account.account_invoices").id, reports)
        self.company.doc_replace_standard = False
        reports = {a["id"] for a in self.env["ir.actions.actions"].get_bindings("account.move").get("report", [])}
        self.assertIn(self.env.ref("account.account_invoices").id, reports, "switched off: Odoo's own come back")

    def test_each_document_shows_only_on_its_records(self):
        invoice_reports = self.report("action_report_sales_invoice") | self.report("action_report_commercial_invoice") \
            | self.report("action_report_credit_note")
        valid = invoice_reports.get_valid_action_reports("account.move", self.export_invoice().ids)
        self.assertEqual(valid, [self.report("action_report_commercial_invoice").id])
        valid = invoice_reports.get_valid_action_reports("account.move", self.local_invoice().ids)
        self.assertEqual(valid, [self.report("action_report_sales_invoice").id])
        out = self.picking(self.warehouse.out_type_id, self.partner_a)
        export_out = self.picking(self.warehouse.out_type_id, self.r4)
        receipt = self.picking(self.warehouse.in_type_id, self.partner_b)
        picking_reports = self.Report.browse([self.report(x).id for x in (
            "action_report_delivery_note", "action_report_export_packing_list", "action_report_grn",
            "action_report_goods_return_note", "action_report_replacement_note", "action_report_transfer_form")])
        names = lambda ids: set(self.Report.browse(ids).mapped("name"))
        self.assertEqual(names(picking_reports.get_valid_action_reports("stock.picking", out.ids)), {"Delivery Note"})
        self.assertEqual(names(picking_reports.get_valid_action_reports("stock.picking", export_out.ids)),
                         {"Delivery Note", "Export Packing List"})
        self.assertEqual(names(picking_reports.get_valid_action_reports("stock.picking", receipt.ids)),
                         {"Goods Received Note"})

    def test_send_and_print_uses_the_document_for_the_invoice(self):
        Send = self.env["account.move.send"]
        self.assertEqual(Send._get_default_pdf_report_id(self.local_invoice()), self.report("action_report_sales_invoice"))
        self.assertEqual(Send._get_default_pdf_report_id(self.export_invoice(post=True)),
                         self.report("action_report_commercial_invoice"))
        refund = self.invoice(self.partner_a, [{"product_id": self.product_a.id, "quantity": 1, "price_unit": 199.0}],
                              move_type="out_refund")
        self.assertEqual(Send._get_default_pdf_report_id(refund), self.report("action_report_credit_note"))
        self.company.doc_replace_standard = False
        self.assertEqual(Send._get_default_pdf_report_id(self.local_invoice()), self.env.ref("account.account_invoices"))

    def test_emails_attach_the_companys_documents(self):
        template = self.env.ref("sale.email_template_edi_sale")
        self.assertIn(self.report("action_report_estimate"), template.report_template_ids)
        self.assertNotIn(self.env.ref("sale.action_report_saleorder"), template.report_template_ids)
        po_template = self.env.ref("purchase.email_template_edi_purchase_done")
        self.assertIn(self.report("action_report_purchase_order"), po_template.report_template_ids)

    # ------------------------------------------------------------ sales
    def test_sales_invoice_on_the_letterhead_with_payment_details(self):
        html = self.render("action_report_sales_invoice", self.local_invoice())
        for text in ("RINZ PRODUCTS LIMITED", "KRA PIN: P052112729P", "Invoice", "982800", "110110",
                     "3000184558", "PRIEKENX", "Amount in words", "Title to goods remains with"):
            self.assertIn(text, html)
        self.assertNotIn("3001232272", html, "a KES invoice lists the KES account, not the USD one")

    def test_estimate_and_pro_forma_from_a_quotation(self):
        order = self.env["sale.order"].create({"partner_id": self.partner_a.id, "order_line": [
            Command.create({"product_id": self.product_a.id, "product_uom_qty": 12, "price_unit": 199.0})]})
        estimate = self.render("action_report_estimate", order)
        self.assertIn("Estimate", estimate)
        self.assertIn("valid for <strong>7 days</strong>", estimate)
        proforma = self.render("action_report_proforma", order)
        self.assertIn("Pro Forma Invoice", proforma)
        self.assertIn("advance payment request", proforma)
        self.assertIn("982800", proforma)

    def test_credit_note_and_receipt(self):
        invoice = self.local_invoice()
        refund = self.invoice(self.partner_a, [{"product_id": self.product_a.id, "quantity": 1, "price_unit": 199.0}],
                              move_type="out_refund")
        self.assertIn("no cash refunds", self.render("action_report_credit_note", refund))
        self.env["account.payment.register"].with_context(active_model="account.move", active_ids=invoice.ids).create(
            {"payment_date": "2026-11-03"})._create_payments()
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        self.assertTrue(self.report("action_report_sales_receipt").get_valid_action_reports("account.move", invoice.ids))
        html = self.render("action_report_sales_receipt", invoice)
        self.assertIn("Sales Receipt", html)
        self.assertIn("PAID", html)
        self.assertIn("03/11/2026", html, "the date it was paid")

    # ------------------------------------------------------------ export
    def test_commercial_invoice_in_both_currencies_at_the_cbk_rate(self):
        invoice = self.export_invoice()
        self.assertTrue(invoice.doc_is_export)
        self.assertEqual(invoice.doc_foreign_currency_id, self.usd)
        self.assertAlmostEqual(invoice.doc_exchange_rate, 125.0, places=4)
        self.assertEqual(invoice._doc_two_currencies(130.0), (130.0, 1.04))
        html = self.render("action_report_commercial_invoice", invoice)
        for text in ("Commercial Invoice", "1 USD = 125 KES", "SM#56791", "34013000", "R4 DISTRIBUTORS LIMITED",
                     "EXP/03/2022", "157041913", "15,600.00", "124.80", "404.00", "3001232272", "PRIEKENX",
                     "Telegraphic Transfer"):
            self.assertIn(text, html)
        self.assertIn("60", html, "net weight: 120 x 0.5 kg")

    def test_the_rate_can_be_typed_over_inline(self):
        invoice = self.export_invoice()
        invoice.doc_exchange_rate = 130.0
        self.assertEqual(invoice.doc_exchange_rate, 130.0, "an agreed rate stays")
        self.assertEqual(invoice._doc_two_currencies(130.0), (130.0, 1.0))
        self.assertIn("1 USD = 130 KES", self.render("action_report_commercial_invoice", invoice))

    def test_a_usd_invoice_takes_the_typed_rate_into_its_accounts(self):
        invoice = self.export_invoice(currency=self.usd)
        self.assertAlmostEqual(invoice.doc_exchange_rate, 125.0, places=4)
        invoice.doc_exchange_rate = 120.0
        self.assertAlmostEqual(invoice.invoice_currency_rate, 1 / 120.0, places=8)
        self.assertEqual(invoice._doc_two_currencies(10.0), (1200.0, 10.0))

    def test_export_packing_list_counts_cartons_and_weights(self):
        delivery = self.picking(self.warehouse.out_type_id, self.r4, qty=24, doc_pallets=1, doc_cbm=0.8)
        self.assertTrue(delivery.doc_is_export)
        totals = delivery._doc_totals()
        self.assertEqual((totals["cartons"], totals["net"], round(totals["gross"], 2)), (2, 12.0, 13.44))
        html = self.render("action_report_export_packing_list", delivery)
        for text in ("Export Packing List", "34013000", "13.44", "0.8 CBM", "customs authorities"):
            self.assertIn(text, html)

    def test_packing_follows_the_product_and_keeps_what_is_typed(self):
        delivery = self.picking(self.warehouse.out_type_id, self.r4, qty=30)
        move = delivery.move_ids
        self.assertEqual((move.doc_cartons, move.doc_net_weight), (3, 15.0), "30 / 12 per carton, rounded up")
        move.doc_cartons = 4
        move.doc_pallet = "1"
        move.doc_gross_weight = 18.2
        self.product_a.doc_net_weight = 0.6
        self.assertEqual((move.doc_cartons, move.doc_gross_weight), (4, 18.2), "what was typed stays")
        self.assertAlmostEqual(move.doc_net_weight, 18.0, msg="what was not typed follows the product")
        move.doc_cartons = 0
        self.assertEqual(move.doc_cartons, 3, "0 goes back to the product's figure")
        move.doc_net_weight = 18.0  # the product's own figure: not kept as typed
        self.assertFalse(move.doc_net_typed)
        self.assertEqual((delivery.doc_total_cartons, round(delivery.doc_total_gross, 2)), (3, 18.2))
        self.assertFalse(delivery.doc_packing_warning)
        html = self.render("action_report_export_packing_list", delivery)
        self.assertIn("18.2", html)
        self.assertIn("0.5 L", html)

    def test_packing_says_which_products_lack_data(self):
        bare = self.env["product.product"].create({"name": "Bleach 20 Litre", "is_storable": True})
        delivery = self.picking(self.warehouse.out_type_id, self.r4)
        delivery.move_ids = [Command.create({
            "product_id": bare.id, "product_uom_qty": 2, "product_uom": bare.uom_id.id,
            "location_id": delivery.location_id.id, "location_dest_id": delivery.location_dest_id.id})]
        self.assertIn("Bleach 20 Litre", delivery.doc_packing_warning)
        self.assertNotIn(self.product_a.name, delivery.doc_packing_warning)
        self.assertEqual(bare._doc_pack_size(), "20 L", "read from the name")
        self.assertIn(bare.product_tmpl_id, self.env["product.template"].search(
            delivery.action_doc_open_products()["domain"]))

    def test_print_buttons_print_the_document_for_the_record(self):
        delivery = self.picking(self.warehouse.out_type_id, self.r4)
        action = delivery.with_context(doc_code="export_packing_list").action_doc_print()
        self.assertEqual(action["report_name"], "odin_documents.report_export_packing_list")
        self.assertFalse(self.company.external_report_layout_id)
        self.assertEqual(self.local_invoice().action_print_pdf().get("report_name"), "odin_documents.report_sales_invoice",
                         "the invoice's own Print prints the company's invoice, without Odoo's layout wizard first")
        with self.assertRaises(UserError):
            delivery.with_context(doc_code="nothing").action_doc_print()

    # ------------------------------------------------------------ stock and purchases
    def test_stock_documents(self):
        delivery = self.picking(self.warehouse.out_type_id, self.partner_a)
        html = self.render("action_report_delivery_note", delivery)
        self.assertIn("Delivery Note", html)
        self.assertIn("We are authorised to accept goods on behalf of the buyer", html)
        receipt = self.picking(self.warehouse.in_type_id, self.partner_b, doc_supplier_ref="DN-7781")
        html = self.render("action_report_grn", receipt)
        self.assertIn("Goods Received Note", html)
        self.assertIn("DN-7781", html)
        back = self.picking(self.warehouse.out_type_id, self.partner_b, qty=2, return_id=receipt.id, doc_reason="Leaking")
        html = self.render("action_report_goods_return_note", back)
        self.assertIn("Goods Return Note", html)
        self.assertIn("Leaking", html)
        self.assertIn(receipt.name, html, "against the GRN")
        returned = self.picking(self.warehouse.in_type_id, self.partner_a, qty=1, return_id=delivery.id)
        self.assertIn("Goods Replacement Note", self.render("action_report_replacement_note", returned))
        transfer = self.picking(self.warehouse.int_type_id, False, qty=5)
        self.assertIn("Transfer Form", self.render("action_report_transfer_form", transfer))

    def test_a_storekeeper_prints_the_delivery_note_of_a_sale(self):
        order = self.env["sale.order"].create({"partner_id": self.partner_a.id, "client_order_ref": "LPO-77",
                                               "order_line": [Command.create({"product_id": self.product_a.id,
                                                                              "product_uom_qty": 6, "price_unit": 199.0})]})
        order.action_confirm()
        storekeeper = new_test_user(self.env, "doc_storekeeper", groups="stock.group_stock_user")
        html, _kind = self.Report.with_user(storekeeper)._render_qweb_html(
            "odin_documents.action_report_delivery_note", order.picking_ids.ids)
        self.assertIn("LPO-77", html.decode(), "the customer's order number, without sales rights")
        self.assertIn(order.name, html.decode())

    def test_count_sheet_is_one_document_for_the_lines_counted(self):
        Quant = self.env["stock.quant"]
        Quant._update_available_quantity(self.product_a, self.warehouse.lot_stock_id, 50)
        quants = Quant.search([("product_id", "=", self.product_a.id), ("location_id", "=", self.warehouse.lot_stock_id.id)])
        quants.inventory_quantity = 48
        html = self.render("action_report_stock_count", quants)
        self.assertIn("Stock Sheet - Physical Count", html)
        self.assertIn("-2", html, "the variance")
        before = self.env["odin.document.log"].search_count([])
        self.Report._render_qweb_pdf("odin_documents.action_report_stock_count", quants.ids)
        self.assertEqual(self.env["odin.document.log"].search_count([]), before + 1, "one sheet, one entry")

    def test_rfq_purchase_order_and_requisition(self):
        order = self.env["purchase.order"].create({"partner_id": self.partner_b.id, "order_line": [
            Command.create({"product_id": self.product_a.id, "product_qty": 10, "price_unit": 50.0})]})
        self.assertFalse(self.company.external_report_layout_id)
        self.assertEqual(order.print_quotation().get("report_name"), "odin_documents.report_rfq",
                         "Print RFQ prints the company's RFQ, without asking to choose Odoo's layout first")
        self.assertIn("Request for Quotation", self.render("action_report_rfq", order))
        order.button_confirm()
        html = self.render("action_report_purchase_order", order)
        self.assertIn("Purchase Order", html)
        self.assertIn("binding contract", html)
        department = self.env["hr.department"].create({"name": "Production", "doc_code": "PRD"})
        self.env["hr.employee"].create({"name": "Requester", "user_id": self.env.user.id, "department_id": department.id})
        request = self.env["purchase.request"].create({"requested_by": self.env.user.id, "line_ids": [
            Command.create({"product_id": self.product_a.id, "product_qty": 4, "product_uom_id": self.product_a.uom_id.id})]})
        html = self.render("action_report_requisition", request)
        self.assertIn("Requisition Form", html)
        self.assertIn("Production", html)

    # ------------------------------------------------------------ memo
    def test_memo_is_numbered_locked_and_acknowledged(self):
        department = self.env["hr.department"].create({"name": "Human Resources", "doc_code": "HR"})
        memo = self.env["odin.document.memo"].create({
            "department_id": department.id, "subject": "Staff meeting", "body": "<p>Friday 10am.</p>",
            "needs_acknowledgement": True, "date": fields.Date.to_date("2026-11-11")})
        reader = new_test_user(self.env, "memo_reader", groups="base.group_user")
        with self.assertRaises(UserError):
            memo.with_user(reader).action_issue()
        memo.action_issue()
        self.assertRegex(memo.name, r"^RINZ/MEMO/HR/2026/\d{3}$")
        with self.assertRaises(UserError):
            memo.subject = "Changed"
        with self.assertRaises(UserError):
            memo.unlink()
        memo.with_user(reader).action_acknowledge()
        self.assertTrue(memo.with_user(reader).acknowledged)
        html = self.render("action_report_memo", memo)
        self.assertIn("Staff meeting", html)
        self.assertIn(memo.name, html)
        self.assertIn("Acknowledgement of receipt", html)

    # ------------------------------------------------------------ the register
    def test_printing_registers_the_document(self):
        invoice = self.local_invoice()
        self.Report._render_qweb_pdf("odin_documents.action_report_sales_invoice", invoice.ids)
        log = self.env["odin.document.log"].search([("res_model", "=", "account.move"), ("res_id", "=", invoice.id)])
        self.assertEqual(len(log), 1)
        self.assertEqual((log.doc_type_id.code, log.partner_id, log.amount),
                         ("sales_invoice", invoice.partner_id, invoice.amount_total))
        self.assertTrue(re.search(r"INV", log.name or ""))
