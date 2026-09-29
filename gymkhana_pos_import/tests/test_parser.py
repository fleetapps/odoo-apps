"""Parser tests: golden file + mutations.

They need no database. Inside Odoo they run with the module's tests
(``--test-tags /gymkhana_pos_import``); outside Odoo run them with
``python -m unittest discover -s gymkhana_pos_import/tests -p 'test_parser.py'``.
"""
import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "sales_register_2026-09-27.pdf"
SEED_CSV = HERE.parent / "data" / "pos_item_map_seed.csv"

try:
    from odoo.tests import BaseCase as TestCase, tagged
    from ..lib import gymkhana_parser as gp
    from . import pdf_tools
except ImportError:  # standalone run, without Odoo
    import unittest
    TestCase = unittest.TestCase

    def tagged(*_tags):
        return lambda cls: cls
    sys.path[:0] = [str(HERE.parent / "lib"), str(HERE)]
    import gymkhana_parser as gp
    import pdf_tools

D = Decimal


@tagged("post_install", "-at_install", "gymkhana_pos_import")
class TestGoldenFile(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = FIXTURE.read_bytes()
        cls.res = gp.parse(cls.data)

    def test_header(self):
        h = self.res["header"]
        self.assertEqual(h["date_from"], date(2026, 9, 27))
        self.assertEqual(h["date_to"], date(2026, 9, 27))
        self.assertEqual(h["outlet"], "Dhostana Ventures Ltd")
        self.assertEqual(self.res["page_count"], 4)

    def test_totals(self):
        self.assertEqual(len(self.res["lines"]), 115)
        self.assertEqual([g["name"] for g in self.res["groups"]], ["Banda Bar", "Bulls Eye", "Main Bar"])
        gt = self.res["grand_total"]
        self.assertEqual(gt["net"], D("217205.00"))
        self.assertEqual(gt["tax"], D("33131.88"))
        self.assertEqual(gt["amount"], D("184073.12"))
        self.assertEqual(gt["disc"], D("0.00"))
        self.assertEqual(sum(ln["net"] for ln in self.res["lines"]), D("217205.00"))
        self.assertEqual(sum(ln["tax"] for ln in self.res["lines"]), D("33131.88"))

    def test_groups(self):
        expected = {
            "Banda Bar": (32, 10, "BB", D("46575.00")),
            "Bulls Eye": (70, 14, "BE", D("162515.00")),
            "Main Bar": (13, 5, "M", D("8115.00")),
        }
        for g in self.res["groups"]:
            n_lines, n_subs, suffix, net = expected[g["name"]]
            lines = [ln for s in g["subgroups"] for ln in s["lines"]]
            self.assertEqual(len(lines), n_lines, g["name"])
            self.assertEqual(len(g["subgroups"]), n_subs, g["name"])
            self.assertEqual(g["total"]["net"], net, g["name"])
            self.assertEqual({ln["bar_suffix"] for ln in lines}, {suffix}, g["name"])
            self.assertEqual({s["bar_suffix"] for s in g["subgroups"]}, {suffix}, g["name"])
            self.assertEqual({ln["group"] for ln in lines}, {g["name"]})

    def test_wrapped_names(self):
        by_page_name = {(ln["page"], ln["name"], ln["unit"]): ln for ln in self.res["lines"]}
        ln = by_page_name[(1, "Smirnoff Black Ice 300Ml", "Bottle")]
        self.assertEqual((ln["qty"], ln["net"]), (D("4.00"), D("1000.00")))
        ln = by_page_name[(1, "Potato Crisps 100Gms ( Gita Foods)", "Each")]
        self.assertEqual(ln["key"], "potato crisps 100gms (gita foods)|each")
        ln = by_page_name[(2, "SPARKLING WATER 500ML", "500ML")]
        self.assertEqual((ln["bar_suffix"], ln["key"]), ("BB", "sparkling water 500ml|500ml"))
        ln = by_page_name[(3, "Watermelon Margarita HH 450", "Glass")]
        self.assertEqual(ln["price_unit"], D("450.00"))
        ln = by_page_name[(4, "Soda Water 500Ml - Plastic", "Bottle")]
        self.assertEqual(ln["net"], D("200.00"))

    def test_same_item_several_units(self):
        black_label = [ln for ln in self.res["lines"] if ln["name"] == "Black Label"]
        self.assertEqual(sorted(ln["unit"] for ln in black_label), ["1Ltr", "375Ml", "Tot"])
        self.assertEqual(len({ln["key"] for ln in black_label}), 3)

    def test_prices(self):
        for ln in self.res["lines"]:
            self.assertTrue(ln["price_exact"], ln["name"])
            self.assertEqual(ln["price_unit"] * ln["qty"], ln["net"], ln["name"])
            self.assertFalse(ln["rate_mismatch"], ln["name"])
        jager = self.res["lines"][0]
        self.assertEqual((jager["name"], jager["unit"], jager["price_unit"]), ("Jagermeister", "Tot", D("190.00")))

    def test_seed_csv_keys(self):
        with SEED_CSV.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 77)
        for row in rows:
            self.assertEqual(gp.make_key(row["pos_name"], row["pos_unit"]), row["pos_key"])
        self.assertEqual({r["pos_key"] for r in rows}, {ln["key"] for ln in self.res["lines"]})

    def test_make_key(self):
        self.assertEqual(gp.make_key("Potato Crisps 100Gms ( Gita Foods)", "Each"),
                         gp.make_key("potato  crisps 100gms (gita foods )", " each"))
        self.assertNotEqual(gp.make_key("SAPRKLING WATER 500ML", "500ML"),
                            gp.make_key("SPARKLING WATER 500ML", "500ML"))
        self.assertEqual(gp.make_key("Black Label", "Tot"), "black label|tot")
        self.assertEqual(gp.make_key("Black Label", "1 Ltr"), "black label|1ltr")

    def test_split_suffix(self):
        self.assertEqual(gp.split_suffix("Heineken 0.0 (BB)"), ("Heineken 0.0", "BB"))
        self.assertEqual(gp.split_suffix("Coke 300 Ml(m)"), ("Coke 300 Ml", "M"))
        self.assertEqual(gp.split_suffix("Green Gram 100Gms (Geeta)"), ("Green Gram 100Gms (Geeta)", None))
        self.assertEqual(gp.split_suffix("Tea(RM)", ("RM",)), ("Tea", "RM"))


@tagged("post_install", "-at_install", "gymkhana_pos_import")
class TestPdfMutations(TestCase):
    """Genuine one-character edits made inside the PDF itself."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = FIXTURE.read_bytes()

    def assertParseError(self, data, *fragments):
        with self.assertRaises(gp.ParseError) as cm:
            gp.parse(data)
        for fragment in fragments:
            self.assertIn(fragment, str(cm.exception))
        return str(cm.exception)

    def test_rewriter_is_faithful(self):
        objects, trailer = pdf_tools._read_objects(self.data)
        res = gp.parse(pdf_tools._write(objects, trailer))
        self.assertEqual((len(res["lines"]), res["grand_total"]["net"]), (115, D("217205.00")))

    def test_subgroup_total_digit(self):
        # the example in the spec
        msg = self.assertParseError(pdf_tools.replace_text(self.data, " 56,216.18", " 56,261.18"))
        self.assertEqual(msg, "Sub group Beer/RTD/0.0/Cans(BE): lines add up to 56,216.18 "
                              "but the report says 56,261.18.")

    def test_line_net_digit(self):
        self.assertParseError(pdf_tools.replace_text(self.data, " 1,520.00", " 1,570.00"),
                              "Jagermeister(BB)", "1,570.00")

    def test_line_amount_digit(self):
        self.assertParseError(pdf_tools.replace_text(self.data, " 1,288.13", " 1,238.13"), "Jagermeister(BB)")

    def test_line_tax_digit(self):
        self.assertParseError(pdf_tools.replace_text(self.data, " 231.87", " 231.97"), "Jagermeister(BB)")

    def test_net_of_both_line_and_its_subtotal(self):
        # a consistent edit of a line (amount+tax+net) is still caught by its sub group total
        data = pdf_tools.replace_text(self.data, " 1,288.13", " 1,388.13")
        data = pdf_tools.replace_text(data, " 1,520.00", " 1,620.00")
        self.assertParseError(data, "Sub group Aperitif/Liqueur/Vermouth(BB)")

    def test_group_total_digit(self):
        self.assertParseError(pdf_tools.replace_text(self.data, " 46,575.00", " 46,574.00"),
                              "Group Banda Bar", "46,574.00")

    def test_grand_total_digits(self):
        self.assertParseError(pdf_tools.replace_text(self.data, " 217,205.00", " 217,206.00"), "Grand Total")
        self.assertParseError(pdf_tools.replace_text(self.data, " 33,131.88", " 33,137.88"), "Grand Total", "tax")

    def test_last_page_deleted(self):
        self.assertParseError(pdf_tools.drop_last_page(self.data), "Grand Total missing")

    def test_quantity_digit_is_flagged(self):
        # Quantity has no total in the report; the rate cross-check flags it.
        res = gp.parse(pdf_tools.replace_text(self.data, " 8.00", " 3.00", page=1))
        flagged = [ln["name"] for ln in res["lines"] if ln["rate_mismatch"]]
        self.assertEqual(flagged, ["Jagermeister"])

    def test_not_a_pdf(self):
        self.assertParseError(b"name,qty\nBalozi,3\n", "isn't a Gymkhana Group Sales Register")

    def test_header_row_missing(self):
        self.assertParseError(pdf_tools.replace_text(self.data, "Quantity", "Qty", page=1),
                              "isn't a Gymkhana Group Sales Register", "column header row")

    def test_page_for_another_day(self):
        data = pdf_tools.replace_text(self.data, " Date From: 09/27/2026 to 09/27/2026  Outlet:",
                                      " Date From: 09/26/2026 to 09/26/2026  Outlet:", page=3)
        self.assertParseError(data, "Page 3", "different date range")


@tagged("post_install", "-at_install", "gymkhana_pos_import")
class TestExhaustiveDigitMutations(TestCase):
    """Change every digit of every net figure (and every amount/tax digit worth
    more than the 0.05 tolerance) and expect a ParseError each time.

    Runs on the extracted words, so ~1,500 mutations take seconds; the PDF
    reading itself is covered by TestPdfMutations.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pages = gp.extract(FIXTURE.read_bytes())
        gp.interpret(cls.pages)  # sanity: untouched pages parse

    def _figures(self):
        """(page index, row index, word index, column) of every money figure."""
        for p, rows in enumerate(self.pages):
            hidx, geo = gp._find_header(rows)
            for r, row in enumerate(rows[hidx + 1:], start=hidx + 1):
                for w, word in enumerate(row):
                    if word["x0"] >= geo["num_x0"] - 2 and gp.NUM_RE.match(word["text"]):
                        col = min(geo["edges"], key=lambda c: abs(geo["edges"][c] - word["x1"]))
                        if col in ("amount", "disc", "tax", "net"):
                            yield p, r, w, col

    def _mutated(self, p, r, w, text):
        pages = list(self.pages)
        rows = list(pages[p])
        row = list(rows[r])
        row[w] = dict(row[w], text=text)
        rows[r] = row
        pages[p] = rows
        return pages

    def test_every_digit(self):
        checked = 0
        for p, r, w, col in self._figures():
            text = self.pages[p][r][w]["text"]
            value = D(text.replace(",", ""))
            for i, ch in enumerate(text):
                if not ch.isdigit():
                    continue
                new = text[:i] + str((int(ch) + 1) % 10) + text[i + 1:]
                if col != "net" and abs(D(new.replace(",", "")) - value) <= gp.TOL:
                    continue  # within the tolerance the spec allows on amount / tax
                with self.assertRaises(gp.ParseError, msg=f"{text} -> {new} ({col}) not caught"):
                    gp.interpret(self._mutated(p, r, w, new))
                checked += 1
        self.assertGreater(checked, 1500)


@tagged("post_install", "-at_install", "gymkhana_pos_import")
class TestAllFixtures(TestCase):
    """Every real PDF dropped in tests/fixtures/ must parse and cross-check.

    Before go-live, add 10-15 more real days (a slow day, a day with a discount,
    a day where a bar was closed): layouts drift when someone edits the report.
    A PDF that must be refused goes in with a sidecar file of the same name and
    the extension .error, holding the expected message fragment.
    """

    def test_fixtures(self):
        pdfs = sorted((HERE / "fixtures").glob("*.pdf"))
        self.assertTrue(pdfs)
        for pdf in pdfs:
            with self.subTest(pdf=pdf.name):
                expected_error = pdf.with_suffix(".error")
                if expected_error.exists():
                    with self.assertRaises(gp.ParseError) as cm:
                        gp.parse(pdf.read_bytes())
                    self.assertIn(expected_error.read_text(encoding="utf-8").strip(), str(cm.exception))
                    continue
                res = gp.parse(pdf.read_bytes())
                self.assertEqual(res["header"]["date_from"], res["header"]["date_to"])
                self.assertEqual(sum(ln["net"] for ln in res["lines"]), res["grand_total"]["net"])
                for ln in res["lines"]:
                    self.assertTrue(ln["bar_suffix"], ln["raw_name"])
