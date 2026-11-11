"""Emails go out with the company's documents: the quotation, pro forma,
RFQ, purchase order and delivery emails attach them in place of Odoo's own.
Uninstalling puts Odoo's back."""

# mail template -> (Odoo's report, the company's report)
MAIL_TEMPLATES = {
    "sale.email_template_edi_sale": ("sale.action_report_saleorder", "odin_documents.action_report_estimate"),
    "sale.email_template_proforma": ("sale.action_report_pro_forma_invoice", "odin_documents.action_report_proforma"),
    "purchase.email_template_edi_purchase": ("purchase.report_purchase_quotation", "odin_documents.action_report_rfq"),
    "purchase.email_template_edi_purchase_done": ("purchase.action_report_purchase_order",
                                                  "odin_documents.action_report_purchase_order"),
    "stock.mail_template_data_delivery_confirmation": ("stock.action_report_delivery",
                                                       "odin_documents.action_report_delivery_note"),
}


def _swap(env, forward=True):
    for template_xmlid, (standard_xmlid, ours_xmlid) in MAIL_TEMPLATES.items():
        template = env.ref(template_xmlid, raise_if_not_found=False)
        standard = env.ref(standard_xmlid, raise_if_not_found=False)
        ours = env.ref(ours_xmlid, raise_if_not_found=False)
        if not (template and standard and ours):
            continue
        old, new = (standard, ours) if forward else (ours, standard)
        # Only where the template still has the report it shipped with.
        if old in template.report_template_ids:
            template.report_template_ids = (template.report_template_ids - old) | new


def post_init_hook(env):
    _swap(env, forward=True)
    for company in env["res.company"].search([("doc_short_name", "=", False)]):
        company.doc_short_name = company._doc_short()


def uninstall_hook(env):
    _swap(env, forward=False)
