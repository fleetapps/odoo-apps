"""Add sale from a photo: what is sent, what fills the window, what is
flagged, and that nothing is saved until the person saves, with the photos."""

import base64
import io
import json
from datetime import timedelta

from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from ..models.stock_photo import LONG_EDGE, _for_model
from .common import PhotoCase, jpeg, message


@tagged("post_install", "-at_install")
class TestPhoto(PhotoCase):

    def prefill_line(self, result, product):
        return next(line for line in result["prefill"]["lines"] if (line["product"] or {}).get("id") == product.id)

    # ------------------------------------------------------------ reading
    def test_the_chandarana_invoice_fills_the_window(self):
        result, requests = self.read()
        prefill = result["prefill"]
        self.assertEqual(prefill["supplier"]["id"], self.brookside.id, "the branch, a contact under Chandarana")
        self.assertEqual(prefill["supplier"]["display_name"], "Chandarana Supermarket, Hookwood Square - Brookside Grove",
                         "shown as the window's own search shows a branch")
        self.assertEqual((prefill["purchase_ref"], prefill["date"]), ("T5436", self.day.isoformat()))
        self.assertEqual(prefill["payment_term_id"], self.net60.id, "Net 60, which dates it due as printed")
        self.assertEqual(prefill["cu_invoice_no"], "KRACU0100TEST436/1")
        self.assertEqual([line["product"]["id"] for line in prefill["lines"]],
                         [self.shampoo.id, self.mp_1000.id, self.layla_shampoo.id],
                         "the shampoo, the 1000 ml bottle and the baby shampoo: no near-miss")
        self.assertEqual([(line["qty"], line["cost"], line["tax_id"]) for line in prefill["lines"]],
                         [(6, 285.0, self.vat.id), (24, 93.0, self.vat.id), (6, 350.0, self.vat.id)],
                         "rates as printed, with the VAT that reads them as including it")
        self.assertEqual([line["photo"]["level"] for line in prefill["lines"]], ["sure"] * 3)
        self.assertFalse(prefill["pending"], "nothing for the model to choose")
        self.assertFalse(self.texts(prefill["flags"], "warn"), "nothing to look at in the head")
        shampoo = prefill["lines"][0]["photo"]
        self.assertEqual(shampoo["printed"], "HAIR SHAMPOO AVOCADO & GRAPESEED 500 ML", "shown beside the item")
        self.assertIn("Not Avocado & Grapeseed Conditioner 500 Ml: a conditioner; the invoice says shampoo",
                      self.texts(shampoo["flags"]), "the near-miss ruled out, and why")
        self.assertIn("a bulk item", self.texts(prefill["lines"][1]["photo"]["flags"]))
        # One request: the photo, upright and JPEG, the schema, low effort.
        self.assertEqual(len(requests), 1)
        params = requests[0]
        self.assertEqual(params["model"], "claude-opus-5-5")
        self.assertEqual(params["output_config"]["effort"], "low")
        self.assertEqual(params["output_config"]["format"]["type"], "json_schema")
        content = params["messages"][0]["content"]
        self.assertEqual([block["type"] for block in content], ["text", "image", "text"])
        self.assertEqual(content[1]["source"]["media_type"], "image/jpeg")
        self.assertIn(self.env.company.name, content[2]["text"], "the seller, so the customer is the other party")
        run = self.env["odin.ai.run"].search([], limit=1)
        self.assertEqual((run.feature, run.status), ("stock_photo", "ok"))
        # Nothing saved: no sale, no book line.
        self.assertFalse(self.env["odin.stock.sale"].search([("sale_ref", "=", "T5436")]))
        photo = self.env["odin.stock.photo"].browse(result["photo"]["id"])
        self.assertEqual((len(photo.attachment_ids), photo.sale_id), (1, self.env["odin.stock.sale"]))
        self.assertEqual(json.loads(photo.reading)["invoice_no"], "T5436", "what was read is kept")

    def test_a_retried_request_reuses_the_reading_and_read_again_reads_afresh(self):
        result, requests = self.read()
        photo_id = result["photo"]["id"]
        self.Book.book_sale_photo_read([], photo_id=photo_id, warehouse_id=self.wh.id)
        self.assertEqual(len(requests), 1, "the same pages: the stored answer, not a second bill")
        self.mock_api(message(self.reading(invoice_no="T5437")))
        again = self.Book.book_sale_photo_read([], photo_id=photo_id, attempt=1, warehouse_id=self.wh.id)
        self.assertEqual(again["prefill"]["purchase_ref"], "T5437")

    def test_a_second_page_is_added_to_the_first(self):
        result, _requests = self.read(self.reading(page_count=2))
        self.assertIn("page 2 is not in the photos", self.texts(result["prefill"]["flags"], "warn"))
        lines = self.reading()["lines"] + [dict(self.line("COCONUT ALOE SHAMPOO 500ML", 12, 280.0), photo=2)]
        requests = self.mock_api(message(self.reading(lines, page_count=2, pages=[
            {"photo": 1, "page": 1}, {"photo": 2, "page": 2}])))
        both = self.Book.book_sale_photo_read([{"name": "page2.jpg", "data": jpeg(colour=(240, 240, 240))}],
                                              photo_id=result["photo"]["id"], warehouse_id=self.wh.id)
        self.assertEqual(len(both["photo"]["files"]), 2)
        self.assertEqual([b["type"] for b in requests[0]["messages"][0]["content"]],
                         ["text", "image", "text", "image", "text"], "both pages read together")
        self.assertNotIn("page 2", self.texts(both["prefill"]["flags"]))
        self.assertEqual(both["prefill"]["lines"][3]["product"]["id"], self.coconut_shampoo.id)

    def test_photos_are_sent_upright_and_no_larger_than_the_model_reads(self):
        from PIL import Image  # noqa: PLC0415

        out = io.BytesIO()
        image = Image.new("RGB", (4000, 3000), (200, 200, 200))
        exif = image.getexif()
        exif[0x0112] = 6  # taken with the phone turned: shown rotated 90 degrees
        image.save(out, "PNG", exif=exif)
        sent = Image.open(io.BytesIO(_for_model(out.getvalue())))
        self.assertEqual(sent.format, "JPEG")
        self.assertEqual(max(sent.size), LONG_EDGE)
        self.assertGreater(sent.size[1], sent.size[0], "turned upright: portrait, as the paper")

    def test_only_photos_and_pdfs_are_taken(self):
        self.mock_api()
        with self.assertRaisesRegex(UserError, "not a photo"):
            self.Book.book_sale_photo_read([{"name": "notes.txt", "data": base64.b64encode(b"hello").decode()}])
        with self.assertRaisesRegex(UserError, "At most 6 photos"):
            self.Book.book_sale_photo_read([{"name": f"p{i}.jpg", "data": jpeg()} for i in range(7)])

    def test_nothing_is_sent_without_consent_and_the_window_says_why(self):
        self.env.company.odin_ai_consent = False
        requests = self.mock_api()
        self.assertIn("allow sending", self.Book.book_sale_options()["photo"]["blocker"])
        with self.assertRaisesRegex(UserError, "allow sending"):
            self.Book.book_sale_photo_read([{"name": "invoice.jpg", "data": jpeg()}])
        self.assertFalse(requests)
        self.env.company.odin_ai_consent = True
        self.assertFalse(self.Book.book_sale_options()["photo"]["blocker"])

    # ------------------------------------------------------------ matching and choosing
    def test_a_line_between_sizes_is_chosen_by_the_model_among_those_only(self):
        lines = [self.line("MULTIPURPOSE REGULAR", 24, 93.0), self.line("MULTIPURPOSE REGULAR BULK", 20, 70.0)]
        result, _requests = self.read(self.reading(lines))
        prefill = result["prefill"]
        self.assertEqual(prefill["lines"][1]["product"]["id"], self.mp_bulk.id, "bulk only where it says bulk")
        self.assertEqual(prefill["pending"], [0], "the first line has no size: three bottles it could be")
        self.assertEqual(prefill["lines"][0]["photo"]["level"], "check")

        def choose(params):
            asked = json.loads(params["messages"][0]["content"].split("Lines:\n", 1)[1])
            self.assertEqual([l["line"] for l in asked], [1])
            codes = {item["code"]: item["letter"] for item in asked[0]["items"]}
            self.assertNotIn("FIN-BULK-020", codes, "the bulk item is never offered for a line without bulk")
            self.assertEqual(asked[0]["items"][0]["customer_price"] if asked[0]["items"][0]["code"] == "FIN-PAK-030"
                             else 93.0, 93.0)
            return message({"choices": [
                {"line": 1, "choice": codes["FIN-PAK-030"], "sure": True, "reason": "93.00 is the 1000 ml price."},
                {"line": 2, "choice": "Z", "sure": True, "reason": "Invented."},
            ]})

        requests = self.mock_api(choose)
        chosen = self.Book.book_sale_photo_choose(result["photo"]["id"], warehouse_id=self.wh.id)["prefill"]
        self.assertEqual(requests[0]["output_config"]["effort"], "medium")
        self.assertNotIn("image", json.dumps(requests[0]["messages"]), "no photo in the second request")
        line = chosen["lines"][0]
        self.assertEqual(line["product"]["id"], self.mp_1000.id)
        self.assertEqual(line["photo"]["level"], "likely")
        self.assertIn("Chosen between 3 similar items: 93.00 is the 1000 ml price.", self.texts(line["photo"]["flags"]))
        self.assertEqual(chosen["lines"][1]["product"]["id"], self.mp_bulk.id, "a line not asked about stays")
        self.assertFalse(chosen["pending"])
        self.mock_api()
        self.Book.book_sale_photo_choose(result["photo"]["id"])  # nothing left: no second request

    def test_a_choice_the_model_is_not_sure_of_stays_to_check(self):
        result, _requests = self.read(self.reading([self.line("LAYLA BABY", 6, 350.0)]))
        self.assertEqual(result["prefill"]["pending"], [0], "shampoo or lotion: the invoice does not say")
        self.mock_api(message({"choices": [{"line": 1, "choice": "A", "sure": False, "reason": "Could be either."}]}))
        line = self.Book.book_sale_photo_choose(result["photo"]["id"])["prefill"]["lines"][0]
        self.assertEqual(line["photo"]["level"], "check")
        self.assertTrue(line["photo"]["choices"], "the other one offered beside it")

    def test_an_item_not_in_the_catalogue_is_left_to_pick(self):
        result, _requests = self.read(self.reading([self.line("HAIR FOOD 250G", 6, 150.0)]))
        line = result["prefill"]["lines"][0]
        self.assertFalse(line["product"])
        self.assertEqual(line["query"], "HAIR FOOD 250G", "what was printed, in the item box to search from")
        self.assertIn("No item in the catalogue matches", self.texts(line["photo"]["flags"], "warn"))

    def test_a_charge_is_matched_as_not_stock_with_its_description(self):
        result, _requests = self.read(self.reading(self.reading()["lines"] + [self.line("Delivery", 1, 500.0)]))
        line = result["prefill"]["lines"][3]
        self.assertEqual((line["product"]["id"], line["product"]["stock"]), (self.delivery.id, False))
        self.assertEqual(line["note"], "Delivery")

    # ------------------------------------------------------------ checks
    def test_a_rate_off_the_customers_pricelist_is_flagged(self):
        self.pricelist.item_ids.filtered(lambda i: i.product_tmpl_id == self.shampoo.product_tmpl_id).fixed_price = 300
        result, _requests = self.read()
        line = self.prefill_line(result, self.shampoo)
        self.assertEqual(line["cost"], 285.0, "the invoice's rate is what goes in, not the pricelist's")
        self.assertEqual(line["photo"]["level"], "check")
        self.assertIn("The invoice's rate is 285.00; the Chandarana price is 300.00.",
                      self.texts(line["photo"]["flags"], "warn"))
        self.assertEqual((line["photo"]["price"], line["photo"]["pricelist"]), (300.0, "Chandarana"))
        other = self.prefill_line(result, self.mp_1000)
        self.assertNotIn("price is", self.texts(other["photo"]["flags"]), "a rate at its price is not flagged")

    def test_the_pricelist_is_compared_as_the_vat_reads_the_price(self):
        """A VAT that reads prices before VAT: the rate is entered before VAT,
        and compared with the pricelist the same way."""
        self.vat.active = False
        self.shampoo.taxes_id = self.vat_net
        item = self.pricelist.item_ids.filtered(lambda i: i.product_tmpl_id == self.shampoo.product_tmpl_id)
        item.fixed_price = 245.69  # before VAT: 285.00 with it
        result, _requests = self.read(self.reading([self.line("HAIR SHAMPOO AVOCADO & GRAPESEED 500 ML", 6, 285.0)]))
        line = result["prefill"]["lines"][0]
        self.assertEqual((line["tax_id"], line["cost"]), (self.vat_net.id, 245.69))
        self.assertIn("entered as 245.69", self.texts(line["photo"]["flags"], "info"))
        self.assertNotIn("price is", self.texts(line["photo"]["flags"]), "285.00 with VAT is 245.69 before it")
        item.fixed_price = 285.0
        result, _requests = self.read(self.reading([self.line("HAIR SHAMPOO AVOCADO & GRAPESEED 500 ML", 6, 285.0)]),
                                      files=[{"name": "again.jpg", "data": jpeg(colour=(1, 2, 3))}])
        self.assertIn("The invoice's rate is 245.69; the Chandarana price is 285.00.",
                      self.texts(result["prefill"]["lines"][0]["photo"]["flags"], "warn"),
                      "285.00 before VAT is not the invoice's 285.00 with it")

    def test_misread_numbers_handwriting_and_what_is_unclear_are_flagged(self):
        lines = [
            self.line("HAIR SHAMPOO AVOCADO & GRAPESEED 500 ML", 6, 285.0, amount=1170.0),
            self.line("MULTIPURPOSE REG 1000 ml", 24, 93.0, handwriting="24 crossed out, 20 written"),
            self.line("LAYLA BABY SHAMPOO 500 ML", 6, None, unclear="rate under a fold"),
        ]
        result, _requests = self.read(self.reading(lines))
        flags = [self.texts(line["photo"]["flags"], "warn") for line in result["prefill"]["lines"]]
        self.assertIn("6 × 285.00 is 1,710.00; the invoice prints 1,170.00", flags[0])
        self.assertIn("Written by hand on this line: 24 crossed out, 20 written", flags[1])
        self.assertEqual(result["prefill"]["lines"][1]["qty"], 24, "the printed quantity: the person decides")
        self.assertIn("The rate could not be read", flags[2])
        self.assertIn("Not clear on the photo: rate under a fold", flags[2])
        self.assertNotIn("add up to", self.texts(result["prefill"]["flags"]),
                         "a line whose amount is not read: the total is not checked")

    def test_lines_that_do_not_add_up_to_the_total_are_flagged(self):
        reading = self.reading()
        reading["totals"]["total"] += 350.0
        result, _requests = self.read(reading)
        self.assertIn("The lines read add up to 6,042.00; the invoice prints 6,392.00. A line may be missing or "
                      "misread.", self.texts(result["prefill"]["flags"], "warn"))
        self.assertEqual(result["prefill"]["total"], 6392.0, "the window compares its own total with it")

    def test_the_head_says_what_does_not_add_up(self):
        reading = self.reading(date=(self.day + timedelta(days=5)).isoformat(),
                               due_date=(self.day + timedelta(days=30)).isoformat(), is_invoice=False,
                               problems=["A stamp covers the last line."])
        result, _requests = self.read(reading)
        flags = self.texts(result["prefill"]["flags"], "warn")
        self.assertIn("in the future", flags)
        self.assertIn("Net 60 dates it", flags, "due as printed is not what the terms give")
        self.assertIn("does not look like a customer invoice", flags)
        self.assertIn("A stamp covers the last line.", flags)

    def test_a_cu_number_already_used_is_flagged(self):
        result, _requests = self.read()
        self.Book.book_sale_save(self.save_values(result))
        again, _requests = self.read(self.reading(invoice_no="T5440"))
        self.assertIn("KRACU0100TEST436/1 is already on invoice T5436", self.texts(again["prefill"]["flags"], "warn"))

    # ------------------------------------------------------------ the customer
    def test_a_branch_that_is_not_a_contact_is_picked_by_hand(self):
        reading = self.reading()
        reading["customer"]["branch"] = "WESTLANDS"
        result, _requests = self.read(reading)
        prefill = result["prefill"]
        self.assertFalse(prefill["supplier"], "not guessed: the person picks")
        self.assertEqual(prefill["supplier_query"], "CHANDARANA SUPERMARKET, WESTLANDS")
        self.assertIn(self.chandarana.id, [c["id"] for c in prefill["customer_choices"]])
        self.assertIn("branch “Westlands”, which is not a contact under Chandarana Supermarket",
                      self.texts(prefill["flags"], "warn"))

    def test_a_customer_not_in_odoo_is_typed_in_the_box(self):
        reading = self.reading()
        reading["customer"] = {"name": "QUICKMART PHOTO TEST", "branch": "", "pin": "P051234567X"}
        result, _requests = self.read(reading)
        prefill = result["prefill"]
        self.assertFalse(prefill["supplier"])
        self.assertEqual((prefill["supplier_query"], prefill["partner_vat"]), ("QUICKMART PHOTO TEST", "P051234567X"))
        self.assertIn("No customer in Odoo matches", self.texts(prefill["flags"], "warn"))

    def test_the_pin_on_file_is_kept(self):
        self.chandarana.vat = "P051111111A"
        reading = self.reading()
        reading["customer"]["pin"] = "P059999999Z"
        result, _requests = self.read(reading)
        self.assertEqual(result["prefill"]["partner_vat"], "P051111111A")
        self.assertIn("P059999999Z", self.texts(result["prefill"]["flags"], "warn"))

    # ------------------------------------------------------------ saving with the photo
    def test_the_photo_goes_with_the_sale_and_on_its_invoice(self):
        result, _requests = self.read()
        saved = self.Book.book_sale_save(self.save_values(result))
        self.assertTrue(saved["posted"])
        sale = self.env["odin.stock.line"].browse(saved["ids"]).stock_sale_id
        photo = self.env["odin.stock.photo"].browse(result["photo"]["id"])
        self.assertEqual(sale.photo_ids, photo)
        self.assertEqual(sale.partner_id, self.brookside)
        invoice = sale.invoice_id
        self.assertEqual(photo.invoice_id, invoice)
        copies = invoice.message_ids.attachment_ids
        self.assertEqual(copies.mapped("checksum"), photo.attachment_ids.mapped("checksum"),
                         "the photo beside the invoice in Accounting")
        self.assertEqual(saved["photos"], 1)

    def test_a_sale_saved_for_posting_keeps_its_photo_and_posting_puts_it_on_the_invoice(self):
        storekeeper = new_test_user(self.env, login="photo_storekeeper",
                                    groups="base.group_user,odin_stock_book.group_stock_book_user")
        Book = self.Book.with_user(storekeeper)
        self.mock_api(message(self.reading()))
        result = Book.book_sale_photo_read([{"name": "invoice.jpg", "data": jpeg()}], warehouse_id=self.wh.id)
        saved = Book.book_sale_save(self.save_values(result))
        self.assertFalse(saved["posted"], "a storekeeper's sale waits for a supervisor")
        lines = self.env["odin.stock.line"].browse(saved["ids"])
        sale = lines.stock_sale_id
        self.assertTrue(sale.photo_ids)
        self.assertEqual(Book.book_sale_photo_files(lines[0].id)["id"], result["photo"]["id"],
                         "the window opened on the draft shows the photo")
        # The supervisor completes it in the window: saving replaces the draft, the photo stays.
        values = self.save_values(result)
        again = self.Book.book_sale_save(values, replace_ids=lines.ids)
        sale = self.env["odin.stock.line"].browse(again["ids"]).stock_sale_id
        self.assertEqual(sale.photo_ids.id, result["photo"]["id"])
        self.assertTrue(sale.invoice_id.message_ids.attachment_ids)

    def test_a_draft_posted_from_the_book_puts_its_photo_on_the_invoice(self):
        result, _requests = self.read()
        saved = self.Book.book_sale_save(self.save_values(result), post=False)
        lines = self.env["odin.stock.line"].browse(saved["ids"])
        self.assertFalse(lines.stock_sale_id.invoice_id)
        lines.action_post()
        invoice = lines.stock_sale_id.invoice_id
        self.assertTrue(invoice)
        self.assertEqual(self.env["odin.stock.photo"].browse(result["photo"]["id"]).invoice_id, invoice)

    def test_photos_of_another_sale_are_refused(self):
        result, _requests = self.read()
        self.Book.book_sale_save(self.save_values(result))
        values = self.save_values(result, purchase_ref="T5441", cu_invoice_no="")
        with self.assertRaisesRegex(UserError, "already with sale T5436"):
            self.Book.book_sale_save(values)
        with self.assertRaisesRegex(UserError, "posted"):
            self.Book.book_sale_photo_read([], photo_id=result["photo"]["id"])

    def test_an_undone_sale_opens_again_with_its_photo(self):
        result, _requests = self.read()
        saved = self.Book.book_sale_save(self.save_values(result))
        undone = self.Book.book_sale_undo(saved["ids"][0])
        self.assertEqual(undone["again"]["photo"]["id"], result["photo"]["id"])
        values = self.save_values(result)
        again = self.Book.book_sale_save(values)
        sale = self.env["odin.stock.line"].browse(again["ids"]).stock_sale_id
        self.assertEqual(sale.photo_ids.id, result["photo"]["id"], "with the sale entered again")
        self.assertTrue(sale.invoice_id.message_ids.attachment_ids, "and on its new invoice")

    def test_photos_never_saved_are_deleted_after_a_while(self):
        result, _requests = self.read()
        photo = self.env["odin.stock.photo"].browse(result["photo"]["id"])
        self.env.cr.execute("UPDATE odin_stock_photo SET create_date = %s WHERE id = %s",
                            (photo.create_date - timedelta(days=31), photo.id))
        photo.invalidate_recordset()
        attachments = photo.attachment_ids
        self.env["odin.stock.photo"]._cron_purge()
        self.assertFalse(photo.exists())
        self.assertFalse(attachments.exists())
