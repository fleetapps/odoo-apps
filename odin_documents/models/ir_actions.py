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


# The reports whose paper format follows the company's document style.
BAND_REPORTS = ("odin_documents.report_sales_invoice", "odin_documents.report_delivery_note",
                "odin_documents.report_commercial_invoice", "odin_documents.report_grn")


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
        # Odoo's delivery slip, printed by the server (auto-print on validating,
        # say): the transfer's own document instead.
        if len(self) == 1 and self.report_name == "stock.report_deliveryslip" and self.env.company.doc_replace_standard:
            ids = docids.ids if isinstance(docids, models.BaseModel) else ([docids] if isinstance(docids, int)
                                                                         else list(docids or []))
            pickings = self.env["stock.picking"].browse(ids).exists()
            ours = pickings[:1] and pickings[:1]._doc_type(pickings[:1]._doc_print_code()).report_id
            if ours:
                return ours.report_action(pickings, data=data, config=False)
        return super().report_action(docids, data=data, config=config)

    def get_paperformat(self):
        """A Band document (sales and commercial invoice, delivery note, GRN) of a company
        on the Band style prints edge to edge, on its own paper format: the
        band reaches the page's edges."""
        paperformat = super().get_paperformat()
        if len(self) == 1 and self.report_name in BAND_REPORTS and self.env.company.doc_invoice_style == "band":
            # The same orientation as the document's own: the commercial invoice is landscape.
            xmlid = "odin_documents.paperformat_band_landscape" if paperformat.orientation == "Landscape" \
                else "odin_documents.paperformat_band"
            return self.env.ref(xmlid, raise_if_not_found=False) or paperformat
        return paperformat

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        ids = [res_ids] if isinstance(res_ids, int) else list(res_ids or [])
        band = ids and report_ref and self._get_report(report_ref)
        if band and band.report_name in BAND_REPORTS:
            # Printed as the record's company, so the paper format follows its style.
            # Only a company the user is in (or the portal, as superuser): Odoo
            # refuses to switch to any other.
            company = self.env[band.model].browse(ids[:1]).exists().company_id
            if company and company != self.env.company and (self.env.su or company in self.env.user.company_ids):
                self = self.with_company(company)
        result = super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
        if ids:
            try:
                report = self._get_report(report_ref)
                if report.report_name.startswith("odin_documents."):
                    with self.env.cr.savepoint():
                        self.env["odin.document.log"]._log_print(report, ids)
            except Exception:  # the register must never stop a document printing
                _logger.warning("Business Documents: could not register %s %s", report_ref, ids, exc_info=True)
        return result
