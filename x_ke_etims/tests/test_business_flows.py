# -*- coding: utf-8 -*-
"""Everyday flows: discounts and down payments, payment types, sellers not
registered for VAT, category item classes, and the checks shown on drafts."""

from odoo import Command, fields
from odoo.tests import tagged

from .common import KeEtimsCase


@tagged("post_install", "-at_install")
class TestBusinessFlows(KeEtimsCase):

    def move(self, lines, move_type="out_invoice", post=True, partner=None, **values):
        move = self.env["account.move"].create({
            "move_type": move_type, "partner_id": (partner or self.customer).id,
            "invoice_date": fields.Date.today(), "invoice_user_id": self.salesperson.id,
            "invoice_line_ids": [Command.create(dict(
                {"quantity": 1, "tax_ids": [Command.set(self.st16.ids)]}, **line))
                for line in lines],
            **values,
        })
        if post:
            move.action_post()
        return move

    def sent_lines(self, move):
        return move._ke_etims_prepare(move.ke_etims_document_id).lines

    def test_a_discount_line_is_folded_into_the_lines_it_reduces(self):
        oil = self.make_product("Cooking oil", 450.0, self.st16)
        move = self.move([
            {"product_id": self.product.id, "price_unit": 1000.0},
            {"product_id": oil.id, "price_unit": 450.0, "quantity": 2},
            {"name": "Discount 10%", "price_unit": -190.0},
        ])
        self.run_queue()
        self.assertEqual(move.ke_etims_state, "sent", move.ke_etims_last_error)
        lines = self.sent_lines(move)
        self.assertEqual(len(lines), 2, "The discount is not a line of its own.")
        self.assertEqual(float(sum(l.taxable_amount for l in lines)), move.amount_total,
                         "KRA gets exactly the invoice total.")
        self.assertTrue(all(l.discount_amount > 0 for l in lines))
        self.assertAlmostEqual(float(sum(l.tax_amount for l in lines)), move.amount_tax, delta=0.02)

    def test_a_down_payment_deducted_on_the_final_invoice(self):
        move = self.move([
            {"product_id": self.product.id, "price_unit": 10000.0},
            {"name": "Down payment", "price_unit": 4000.0, "quantity": -1},
        ])
        self.run_queue()
        self.assertEqual(move.ke_etims_state, "sent", move.ke_etims_last_error)
        self.assertEqual(self.sent_lines(move)[0].taxable_amount, 6960.0)

    def test_a_deduction_larger_than_the_sale_is_refused_before_posting(self):
        draft = self.move([
            {"product_id": self.product.id, "price_unit": 1000.0},
            {"name": "Down payment", "price_unit": 4000.0, "quantity": -1},
        ], post=False)
        self.assertTrue(any("more than" in alert["message"]
                            for alert in (draft.alerts or {}).values()))

    def test_payment_type_is_never_empty(self):
        self.assertFalse(self.ke_company.ke_etims_default_payment_type_id)
        invoice = self.move([{"product_id": self.product.id, "price_unit": 100.0}])
        receipt = self.move([{"product_id": self.product.id, "price_unit": 100.0}],
                            move_type="out_receipt")
        self.assertEqual(invoice.ke_etims_payment_type_id.code, "02")
        self.assertEqual(receipt.ke_etims_payment_type_id.code, "01")

    def test_a_seller_not_registered_for_vat(self):
        self.ke_company.ke_etims_vat_registered = False
        plain = self.move([{"product_id": self.product.id, "price_unit": 500.0,
                            "tax_ids": [Command.clear()]}])
        taxed = self.move([{"product_id": self.product.id, "price_unit": 500.0}])
        self.run_queue()
        self.assertEqual(plain.ke_etims_state, "sent", plain.ke_etims_last_error)
        self.assertEqual(self.sent_lines(plain)[0].tax_type, "D")
        self.assertEqual(taxed.ke_etims_state, "failed")
        self.assertIn("not registered for VAT", taxed.ke_etims_last_error)

    def test_products_take_their_category_item_class(self):
        category = self.env["product.category"].create({"name": "Flour"})
        child = self.env["product.category"].create({"name": "Maize", "parent_id": category.id})
        product = self.env["product.template"].create({"name": "Unga 1kg", "categ_id": child.id})
        self.assertFalse(product.ke_etims_item_class_id)
        category.ke_etims_item_class_id = self.item_class
        self.assertEqual(product.ke_etims_item_class_id, self.item_class)
        other = self.env["ke.etims.item.class"].search([("id", "!=", self.item_class.id)], limit=1)
        product.ke_etims_item_class_id = other
        category.ke_etims_item_class_id = False
        self.assertEqual(product.ke_etims_item_class_id, other, "The product's own choice stays.")

    def test_drafts_say_what_eTIMS_would_refuse(self):
        bare = self.make_product("Gift hamper", 2500.0, self.st16, item_class=False)
        draft = self.move([{"product_id": bare.id, "price_unit": 2500.0}], post=False)
        messages = " ".join(a["message"] for a in (draft.alerts or {}).values())
        self.assertIn("missing its KRA item class", messages)
        bare.ke_etims_item_class_id = self.item_class
        draft.invalidate_recordset(["alerts"])
        self.assertFalse(any(k.startswith("ke_etims") for k in (draft.alerts or {})))

    def test_a_credit_note_made_by_hand_names_its_invoice(self):
        invoice = self.move([{"product_id": self.product.id, "price_unit": 1000.0}])
        self.run_queue()
        refund = self.move([{"product_id": self.product.id, "price_unit": 1000.0}],
                           move_type="out_refund", post=False)
        self.assertIn("Credits Invoice", " ".join(a["message"] for a in refund.alerts.values()))
        refund.write({"reversed_entry_id": invoice.id,
                      "ke_etims_reason_code_id": self.env["ke.etims.code"]._ke_etims_find("32", "06").id})
        refund.action_post()
        self.run_queue()
        self.assertEqual(refund.ke_etims_state, "sent", refund.ke_etims_last_error)
