# -*- coding: utf-8 -*-
"""Both KRA backends against the same fake KRA.

The fake is ``etims.testing.FakeKra`` from the MIT-licensed etims package: an
in-process OSCU server that is stricter than the specification where that
catches integration mistakes -- it refuses an unregistered item, an invoice
number out of sequence, a credit note for an unknown invoice and totals that
do not add up. Every test runs once per backend, so our own client and the
library cannot drift apart unnoticed. Skipped where the package is missing.
"""

from datetime import date
from decimal import Decimal
from unittest import SkipTest

from odoo.tests import tagged

from ..backends import Item, Line, Operator, Purchase, StockIO, StockMaster
from ..backends.kra_direct import API_URLS
from .common import KeEtimsCase

try:
    from etims.testing import FakeKra
except ImportError:  # pragma: no cover
    FakeKra = None

TIN = "P051234567X"


class KraBackendMixin:
    backend = None
    # Odoo's loader only runs test methods defined on the class itself; these
    # are shared by both backends on purpose.
    allow_inherited_tests_method = True

    @classmethod
    def setUpClass(cls):
        if FakeKra is None:
            raise SkipTest("the etims package (and its fake KRA) is not installed")
        super().setUpClass()
        cls.kra = FakeKra().start()
        cls.addClassCleanup(cls.kra.stop)
        cls.device.active = False
        cls.kra_device = cls.make_device(
            cls.ke_company, backend=cls.backend, environment="test",
            serial="OSCU-SERIAL-0001", base_url=cls.kra.url)
        cls.kra_device.action_initialize()

    def setUp(self):
        super().setUp()
        self.kra.reset()

    def sales(self, bhf_id="00"):
        return self.kra.sales(TIN, bhf_id)

    # ------------------------------------------------------------- setup

    def test_device_setup_gets_a_key_and_the_code_list(self):
        device = self.kra_device
        self.assertEqual(device.state, "initialised", device.last_error)
        self.assertTrue(device.sudo().cmc_key)
        self.assertTrue(device.scu_id.startswith("KRACU0"))
        self.assertTrue(device.mrc_no)
        self.assertTrue(device.last_code_sync)
        # FakeKra's code list includes units the seed did not.
        self.assertTrue(self.env["ke.etims.code"]._ke_etims_find("10", "KG"))
        self.assertTrue(self.env["ke.etims.item.class"].search([("code", "=", "4321150000")]))

    def test_a_key_never_reaches_the_logs(self):
        key = self.kra_device.sudo().cmc_key
        logs = self.env["ke.etims.log"].search([("device_id", "=", self.kra_device.id)])
        self.assertTrue(logs)
        for log in logs:
            self.assertNotIn(key, (log.request or "") + (log.response or "") + (log.error or ""))

    def test_an_unapproved_serial_is_refused_and_logged(self):
        with FakeKra(approved_serials={"ONLY-THIS"}) as strict:
            device = self.make_device(self.ke_company, backend=self.backend,
                                      environment="test", serial="OTHER",
                                      base_url=strict.url, bhf_id="05")
            device.action_initialize()
        self.assertEqual(device.state, "error")
        self.assertIn("905", device.last_error)
        self.assertTrue(self.env["ke.etims.log"].search([("device_id", "=", device.id)]))

    # ------------------------------------------------------------ selling

    def test_registration_sale_and_credit_note(self):
        invoice = self.ke_invoice()
        self.run_queue()
        self.assertEqual(invoice.ke_etims_state, "sent", invoice.ke_etims_last_error)
        item_code = self.product.product_tmpl_id.ke_etims_item_code
        self.assertIn(item_code, self.kra.items(TIN, "00"))
        sale = self.sales()[1]
        self.assertEqual(sale["request"]["trdInvcNo"], invoice.name)
        self.assertEqual(sale["request"]["custTin"], "P059876543Y")
        self.assertEqual(float(sale["request"]["totAmt"]), invoice.amount_total)
        self.assertEqual(float(sale["request"]["taxAmtB"]), invoice.amount_tax)
        self.assertEqual(invoice.ke_etims_signature, sale["receipt"]["rcptSign"])
        self.assertEqual(invoice.ke_etims_receipt_no, sale["receipt"]["curRcptNo"])
        self.assertIn("Data=%s00%s" % (TIN, sale["receipt"]["rcptSign"]), invoice.ke_etims_qr_url)

        refund = self.credit(invoice, reason="03")
        self.run_queue()
        self.assertEqual(refund.ke_etims_state, "sent", refund.ke_etims_last_error)
        credit = self.sales()[2]["request"]
        self.assertEqual(credit["rcptTyCd"], "R")
        self.assertEqual(credit["orgInvcNo"], 1)
        self.assertEqual(credit["rfdRsnCd"], "03")

    def test_numbers_continue_per_branch(self):
        branch = self.make_branch("Nairobi Traders - Kisumu", "01")
        branch_device = self.make_device(branch, backend=self.backend, environment="test",
                                         serial="OSCU-SERIAL-0002", bhf_id="01",
                                         base_url=self.kra.url)
        branch_device.action_initialize()
        head = [self.ke_invoice() for _i in range(3)]
        kisumu = [self.ke_invoice(company=branch) for _i in range(2)]
        self.run_queue()
        self.assertEqual([i.ke_etims_kra_invoice_no for i in head], [1, 2, 3])
        self.assertEqual([i.ke_etims_kra_invoice_no for i in kisumu], [1, 2])
        self.assertEqual(sorted(self.sales("00")), [1, 2, 3])
        self.assertEqual(sorted(self.sales("01")), [1, 2])

    def test_a_dropped_connection_is_retried_with_the_same_number(self):
        invoice = self.ke_invoice()
        self.kra.fail_next("/saveTrnsSalesOsdc", drop=True)
        self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "to_send")
        self.assertEqual(document.kra_invoice_no, 1)
        self.assertTrue(document.may_have_been_processed)
        self.run_queue()
        self.assertEqual(document.state, "sent", document.last_error)
        self.assertEqual(sorted(self.sales()), [1])

    def test_a_lost_answer_is_never_signed_twice(self):
        first, second = self.ke_invoice(), self.ke_invoice()
        self.kra.fail_next("/saveTrnsSalesOsdc", lose_answer=True)
        self.run_queue()
        document = first.ke_etims_document_id
        self.assertEqual(document.state, "to_send")
        self.assertFalse(second.ke_etims_kra_invoice_no,
                         "Nothing is numbered after a number whose fate is unknown.")
        self.run_queue()
        # KRA had it: the resend is refused as a duplicate, not signed again.
        self.assertEqual(document.state, "failed")
        self.assertEqual(document.last_error_code, "921")
        self.assertEqual(document.kra_invoice_no, 1)
        self.assertEqual(sorted(self.sales()), [1])
        self.assertEqual(self.kra_device.blocking_document_id, document)
        # The receipt is recorded by hand from the portal, and the queue goes on.
        receipt = self.sales()[1]["receipt"]
        document.write({"receipt_no": receipt["curRcptNo"],
                        "total_receipt_no": receipt["totRcptNo"],
                        "internal_data": receipt["intrlData"],
                        "signature": receipt["rcptSign"]})
        document.action_mark_signed()
        self.run_queue()
        self.assertEqual(second.ke_etims_state, "sent", second.ke_etims_last_error)
        self.assertEqual(second.ke_etims_kra_invoice_no, 2)

    def test_a_rejection_gives_the_number_back(self):
        invoice = self.ke_invoice()
        self.kra.fail_next("/saveTrnsSalesOsdc", result_code="910")
        self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "failed")
        self.assertFalse(document.kra_invoice_no)
        document.action_send_now()
        self.assertEqual(document.state, "sent")
        self.assertEqual(document.kra_invoice_no, 1, "KRA's sequence has no gap.")

    def test_kra_server_errors_are_retried(self):
        invoice = self.ke_invoice()
        self.kra.fail_next("/saveTrnsSalesOsdc", http_status=503)
        self.run_queue()
        self.assertEqual(invoice.ke_etims_document_id.state, "to_send")
        self.run_queue()
        self.assertEqual(invoice.ke_etims_state, "sent")

    # ------------------------------------------- purchases and stock (adapter)

    def operator(self):
        return Operator(id="tester", name="Test User")

    def line(self, seq=1, item_code=None):
        return Line(seq=seq, item_code=item_code or "KE2NTU0000001",
                    item_class_code="5059690800", name="Maize flour",
                    quantity=Decimal("2"), unit_price=Decimal("116"), tax_type="B",
                    supply_amount=Decimal("232.00"), discount_amount=Decimal("0.00"),
                    taxable_amount=Decimal("232.00"), tax_amount=Decimal("32.00"))

    def test_purchase_and_stock_reach_kra(self):
        adapter = self.kra_device._ke_etims_adapter()
        adapter.register_item(Item(
            item_code="KE2NTU0000001", item_class_code="5059690800", product_type="2",
            name="Maize flour", origin_country="KE", packaging_unit="NT",
            quantity_unit="U", tax_type="B", default_price=116, operator=self.operator()))
        adapter.confirm_purchase(Purchase(
            invoice_no=1, lines=[self.line()], purchase_date=date.today(),
            tax_rates=self.kra_device._ke_etims_tax_rates(), operator=self.operator(),
            supplier_pin="P000111222Z", supplier_name="Unga Millers", supplier_invoice_no=77))
        adapter.report_stock_io(StockIO(
            sar_no=1, io_type="02", lines=[self.line()], occurred_on=date.today(),
            operator=self.operator()))
        adapter.save_stock_master(StockMaster(
            item_code="KE2NTU0000001", remaining_quantity=2, operator=self.operator()))
        paths = [path for path, _body in self.kra.requests]
        for path in ("/insertTrnsPurchase", "/insertStockIO", "/saveStockMaster"):
            self.assertIn(path, paths)
        self.assertEqual(adapter.fetch_purchases(), [])
        self.assertEqual(adapter.fetch_imports(), [])


@tagged("post_install", "-at_install")
class TestKraDirect(KraBackendMixin, KeEtimsCase):
    backend = "kra_direct"

    def test_production_urls_and_the_certification_gate(self):
        self.assertEqual(API_URLS["production"], "https://etims-api.kra.go.ke/etims-api")
        self.assertEqual(API_URLS["test"], "https://etims-api-sbx.kra.go.ke/etims-api")
        self.kra_device.certification_ref = "KRA/OSCU/VENDOR/0001"
        self.kra_device.environment = "production"
        self.assertEqual(self.kra_device.state, "draft", "Production needs initialising.")


@tagged("post_install", "-at_install")
class TestKraEtimsLib(KraBackendMixin, KeEtimsCase):
    backend = "kra_etims_lib"

    def test_it_says_it_is_experimental(self):
        self.assertTrue(self.kra_device.experimental)
        self.assertIn("Experimental", self.kra_device.backend_note)
