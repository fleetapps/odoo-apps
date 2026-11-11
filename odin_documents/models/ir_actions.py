import logging

from odoo import models

_logger = logging.getLogger(__name__)

# Odoo's own reports the company's documents take the place of in Print
# menus, while the company has "These documents replace Odoo's" on.
REPLACED_REPORTS = (
    "account.account_invoices",
    "account.account_invoices_without_payment",
    "sale.action_report_saleorder",
    "sale.action_report_pro_forma_invoice",
    "purchase.action_report_purchase_order",
    "purchase.report_purchase_quotation",
    "stock.action_report_delivery",
    "stock.return_label_report",
    "stock.action_report_inventory",
)


class IrActionsActions(models.Model):
    _inherit = "ir.actions.actions"

    def get_bindings(self, model_name):
        result = super().get_bindings(model_name)
        reports = result.get("report")
        if not reports or not self.env.company.doc_replace_standard:
            return result
        hidden = set()
        for xmlid in REPLACED_REPORTS:
            report = self.env.ref(xmlid, raise_if_not_found=False)
            if report:
                hidden.add(report.id)
        kept = [action for action in reports if action.get("id") not in hidden]
        result = dict(result)
        if kept:
            result["report"] = kept
        else:
            result.pop("report")
        return result


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    def report_action(self, docids, data=None, config=True):
        """These documents print on their own letterhead: an administrator is
        not sent to choose Odoo's document layout first."""
        if self and all((report.report_name or "").startswith("odin_documents.") for report in self):
            config = False
        return super().report_action(docids, data=data, config=config)

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        result = super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
        ids = [res_ids] if isinstance(res_ids, int) else list(res_ids or [])
        if ids:
            try:
                report = self._get_report(report_ref)
                if report.report_name.startswith("odin_documents."):
                    with self.env.cr.savepoint():
                        self.env["odin.document.log"]._log_print(report, ids)
            except Exception:  # the register must never stop a document printing
                _logger.warning("Business Documents: could not register %s %s", report_ref, ids, exc_info=True)
        return result
