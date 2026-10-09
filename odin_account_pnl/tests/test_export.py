"""PDF and Excel: the figures of the screen, computed again server-side."""

import io
import openpyxl

from odoo.tests import tagged

from .common import PnlCase


@tagged("post_install", "-at_install")
class TestExport(PnlCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("2026-09-10", [(cls.revenue, -1000.0, {"partner": cls.partner_a}), (cls.cos, 400.0)])

    def test_the_workbook_holds_real_numbers_for_every_line(self):
        options = self.options(unfolded=["L:REV"], comparison={"mode": "previous_period", "periods": 1})
        content, filename, mimetype = self.env["odin.pnl.export"].render(options, "xlsx")
        self.assertTrue(filename.endswith(".xlsx"))
        sheet = openpyxl.load_workbook(io.BytesIO(content)).active
        values = {row[0].value: [cell.value for cell in row[1:]] for row in sheet.iter_rows(min_row=6)}
        net = next(value for key, value in values.items() if key and key.endswith("Net Profit"))
        self.assertEqual(net[0], 600.0)
        account = next(key for key in values if key and self.revenue.name in key)
        self.assertEqual(values[account][0], 1000.0, "unfolded accounts are exported")

    def test_the_payload_marks_notes_as_footnotes(self):
        self.report.add_annotation(self.options(), "L:REV", "One client")
        payload = self.env["odin.pnl.export"]._payload(self.options())
        self.assertEqual(payload["notes"][0]["text"], "One client")
        revenue = next(row for row in payload["rows"] if row["key"] == "L:REV")
        self.assertEqual(revenue["note_refs"], [1])

    def test_the_pdf_renders(self):
        if self.env["ir.actions.report"].get_wkhtmltopdf_state() != "ok":
            self.skipTest("wkhtmltopdf is not installed")
        # Tests render reports as HTML unless asked otherwise (ir_actions_report.py).
        export = self.env["odin.pnl.export"].with_context(force_report_rendering=True)
        content, filename, mimetype = export.render(self.options(), "pdf")
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))
        html = self.env["ir.actions.report"]._render_qweb_html(
            "odin_account_pnl.action_report_pnl", None,
            data={"payload": self.env["odin.pnl.export"]._payload(self.options())})[0]
        self.assertIn(b"Net Profit", html)
