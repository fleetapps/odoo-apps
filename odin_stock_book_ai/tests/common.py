"""The tests' own store: a catalogue with the near-misses the store's
people meet (shampoo and conditioner of one range, a baby shampoo and lotion,
a multipurpose in bottles of three sizes and in bulk), Chandarana Supermarket
with two branches, its pricelist and Net 60.

The Claude API is never called: ``mock_api`` replaces the one network call
(``odin.ai.llm._create_message``) with answers shaped like the Messages
API's, so building the request, logging it and reading the answer run for
real."""

import base64
import copy
import io
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase


def message(answer, usage=None):
    """A Messages API response whose structured answer is ``answer``."""
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": json.dumps(answer)}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": usage or {"input_tokens": 3000, "output_tokens": 600},
    }


def jpeg(width=1200, height=1600, colour=(250, 250, 245)):
    """A photo as a phone sends it: a JPEG, base64 as the window reads it."""
    from PIL import Image  # noqa: PLC0415

    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode()


class PhotoCase(TransactionCase):

    # Names the tests give their own customers: a live namesake is set aside.
    PARTNER_WORDS = ("Chandarana", "Quickmart Photo Test")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids += env.ref("odin_stock_book.group_stock_book_supervisor") \
            + env.ref("product.group_product_pricelist")
        env.company.write({"odin_ai_consent": True, "odin_ai_monthly_tokens": 0})
        params = env["ir.config_parameter"].sudo()
        params.set_param("odin_account_ai.api_key", "sk-ant-test")
        params.set_param("odin_account_ai.model", "claude-opus-5-5")
        # On a copy of a live database: its catalogue and namesake customers
        # set aside, so that the photo is matched against the tests' own.
        # Rolled back with the tests.
        env["product.product"].search([("sale_ok", "=", True)]).product_tmpl_id.active = False
        domain = ["|"] * (len(cls.PARTNER_WORDS) - 1) + [("name", "ilike", w) for w in cls.PARTNER_WORDS]
        namesakes = env["res.partner"].search(domain)
        (namesakes - env["res.company"].search([]).partner_id).filtered(lambda p: not p.user_ids).active = False
        cls.wh = env["stock.warehouse"].search([("company_id", "=", env.company.id)], limit=1)
        unit, litre = env.ref("uom.product_uom_unit"), env.ref("uom.product_uom_litre")
        Category = env["product.category"]
        finished = Category.create({"name": "Finished Goods"})
        hair = Category.create({"name": "Hair Care", "parent_id": finished.id})
        baby = Category.create({"name": "Baby", "parent_id": finished.id})
        cleaning = Category.create({"name": "Cleaning", "parent_id": finished.id})
        bulk = Category.create({"name": "Bulk", "parent_id": finished.id})
        Tax = env["account.tax"]
        # As on the store's invoices: rates include VAT.
        cls.vat = Tax.create({"name": "Test VAT 16% incl", "amount": 16, "type_tax_use": "sale",
                              "price_include_override": "tax_included"})
        cls.vat_net = Tax.create({"name": "Test VAT 16% net", "amount": 16, "type_tax_use": "sale",
                                  "price_include_override": "tax_excluded"})
        Product = env["product.product"]

        def make(code, name, categ, price, uom=unit, storable=True):
            return Product.create({
                "default_code": code, "name": name, "categ_id": categ.id, "uom_id": uom.id, "list_price": price,
                "type": "consu" if storable else "service", "is_storable": storable, "sale_ok": True,
                "standard_price": price / 3, "taxes_id": [Command.set(cls.vat.ids)]})

        cls.shampoo = make("FIN-PAK-040", "Avocado & Grapeseed Shampoo 500 Ml", hair, 285)
        cls.conditioner = make("FIN-PAK-117", "Avocado & Grapeseed Conditioner 500 Ml", hair, 285)
        cls.coconut_shampoo = make("FIN-PAK-044", "Coconut & Aloe Shampoo 500 Ml", hair, 280)
        cls.coconut_conditioner = make("FIN-PAK-119", "Coconut & Aloe Conditioner 500 Ml", hair, 280)
        cls.layla_shampoo = make("FIN-PAK-061", "Layla Baby Shampoo 500 Ml", baby, 350)
        cls.layla_lotion = make("FIN-PAK-062", "Layla Baby Lotion 500 Ml", baby, 350)
        cls.mp_1000 = make("FIN-PAK-030", "Multipurpose Reg 1000 Ml", cleaning, 93)
        cls.mp_500 = make("FIN-PAK-031", "Multipurpose Reg 500 Ml", cleaning, 55)
        cls.mp_5l = make("FIN-PAK-032", "Multipurpose Reg 5 Litre", cleaning, 420)
        cls.mp_bulk = make("FIN-BULK-020", "Multipurpose Regular (Bulk)", bulk, 70, uom=litre)
        cls.delivery = make("SRV-DEL", "Delivery charges", cleaning, 500, storable=False)
        Quant = env["stock.quant"]
        for product in cls.shampoo | cls.conditioner | cls.coconut_shampoo | cls.coconut_conditioner \
                | cls.layla_shampoo | cls.layla_lotion | cls.mp_1000 | cls.mp_500 | cls.mp_5l | cls.mp_bulk:
            Quant._update_available_quantity(product, cls.wh.lot_stock_id, 1000)
        cls.pricelist = env["product.pricelist"].create({"name": "Chandarana", "item_ids": [
            Command.create({"applied_on": "1_product", "product_tmpl_id": product.product_tmpl_id.id,
                            "compute_price": "fixed", "fixed_price": product.list_price})
            for product in cls.shampoo | cls.conditioner | cls.layla_shampoo | cls.layla_lotion | cls.mp_1000]})
        # The company's own Net 60 if it has one: the window takes the first by that name.
        Term = env["account.payment.term"]
        cls.net60 = Term.search([("name", "=ilike", "Net 60"), ("company_id", "in", (False, env.company.id))],
                                order="sequence, id", limit=1) or Term.create({"name": "Net 60", "line_ids": [
            Command.create({"value": "percent", "value_amount": 100, "nb_days": 60})]})
        Partner = env["res.partner"]
        cls.chandarana = Partner.create({"name": "Chandarana Supermarket", "is_company": True,
                                         "property_product_pricelist": cls.pricelist.id})
        cls.brookside = Partner.create({"name": "Hookwood Square - Brookside Grove", "parent_id": cls.chandarana.id,
                                        "type": "delivery"})
        cls.ngara = Partner.create({"name": "Ngara", "parent_id": cls.chandarana.id, "type": "delivery"})
        cls.Book = env["odin.stock.book"]
        cls.day = fields.Date.context_today(cls.Book) - timedelta(days=1)

    # ------------------------------------------------------------ the model
    def mock_api(self, *responses):
        """Answer the next calls with ``responses`` (dicts, or functions of
        the request). Returns the list of requests sent."""
        requests = []
        queue = list(responses)

        def create_message(params, settings):
            requests.append(copy.deepcopy(params))
            if not queue:
                raise AssertionError("Unexpected call to the AI")
            response = queue.pop(0)
            return response(params) if callable(response) else response

        patcher = patch.object(self.registry["odin.ai.llm"], "_create_message", side_effect=create_message)
        patcher.start()
        self.addCleanup(patcher.stop)
        return requests

    def line(self, description, qty, rate, **more):
        return {"photo": 1, "description": description, "code": "", "quantity": qty, "unit": "", "rate": rate,
                "vat": "16.0% S", "amount": round(qty * rate, 2) if rate is not None else None, "handwriting": "",
                "unclear": "", **more}

    def reading(self, lines=None, **more):
        """Invoice 5436 of Chandarana's Brookside Grove branch, as read."""
        lines = lines if lines is not None else [
            self.line("HAIR SHAMPOO AVOCADO & GRAPESEED 500 ML", 6, 285.0),
            self.line("MULTIPURPOSE REG 1000 ml", 24, 93.0),
            self.line("LAYLA BABY SHAMPOO 500 ML", 6, 350.0),
        ]
        return {
            "is_invoice": True,
            "customer": {"name": "CHANDARANA SUPERMARKET", "branch": "HOOKWOOD SQUARE - BROOKSIDE GROVE", "pin": ""},
            "invoice_no": "T5436", "date": self.day.isoformat(), "payment_terms": "Net 60",
            "due_date": (self.day + timedelta(days=60)).isoformat(), "cu_invoice_no": "KRACU0100TEST436/1",
            "prices_include_vat": "yes", "page_count": 1, "pages": [{"photo": 1, "page": 1}],
            "lines": lines,
            "totals": {"before_vat": None, "vat": None, "total": round(sum(l["amount"] or 0 for l in lines), 2)},
            "problems": [], **more,
        }

    def read(self, reading=None, *extra, files=None, **kwargs):
        """Upload a photo and have it read as ``reading``; then, when asked,
        the model's choices ``extra``."""
        requests = self.mock_api(message(reading or self.reading()), *extra)
        result = self.Book.book_sale_photo_read(
            files if files is not None else [{"name": "invoice.jpg", "data": jpeg()}],
            warehouse_id=self.wh.id, **kwargs)
        return result, requests

    def save_values(self, result, **more):
        """What the window sends when the person saves what was filled in."""
        prefill = result["prefill"]
        return {
            "warehouse_id": self.wh.id, "partner_id": prefill["supplier"]["id"],
            "partner_name": prefill["supplier"]["name"], "purchase_ref": prefill["purchase_ref"],
            "date": prefill["date"], "pay_journal_id": False, "partner_vat": prefill["partner_vat"],
            "payment_term_id": prefill["payment_term_id"], "cu_invoice_no": prefill["cu_invoice_no"],
            "lines": [{"product_id": line["product"]["id"], "qty": line["qty"], "cost": line["cost"],
                       "tax_id": line["tax_id"], "note": line["note"]} for line in prefill["lines"]],
            "photo_ids": [result["photo"]["id"]], **more,
        }

    @staticmethod
    def texts(flags, level=None):
        return " | ".join(f["text"] for f in flags if not level or f["level"] == level)
