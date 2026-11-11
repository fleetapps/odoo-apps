from odoo import models


class SaleOrder(models.Model):
    """Estimate (a quotation) and pro forma invoice (an advance payment request)."""

    _name = "sale.order"
    _inherit = ["sale.order", "odin.document.mixin"]

    def _doc_lines(self):
        return self.order_line.filtered(lambda line: not line.is_downpayment)

    def _doc_vat_label(self, line):
        return ", ".join(line.tax_ids.mapped("name")) or "0%"


class PurchaseOrder(models.Model):
    """Request for quotation and purchase order."""

    _name = "purchase.order"
    _inherit = ["purchase.order", "odin.document.mixin"]

    def print_quotation(self):
        """The Print RFQ button prints the company's RFQ."""
        action = super().print_quotation()
        if self.env.company.doc_replace_standard:
            report = self.env.ref("odin_documents.action_report_rfq", raise_if_not_found=False)
            if report:
                return report.report_action(self)
        return action

    def _doc_vat_label(self, line):
        return ", ".join(line.tax_ids.mapped("name")) or "0%"


class PurchaseRequest(models.Model):
    """Requisition: a department asks stores or procurement for items."""

    _name = "purchase.request"
    _inherit = ["purchase.request", "odin.document.mixin"]

    def _doc_department(self):
        self.ensure_one()
        employee = self.env["hr.employee"].sudo().search([("user_id", "=", self.requested_by.id)], limit=1)
        return employee.department_id
