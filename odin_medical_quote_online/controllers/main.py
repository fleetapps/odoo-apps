import logging

from odoo import http
from odoo.exceptions import UserError, ValidationError
from odoo.http import content_disposition, request

_logger = logging.getLogger(__name__)


class OnlineMedicalQuote(http.Controller):
    """The public online quote page, the client's own quote page and its PDF.

    Every answer to the browser is either data or a sentence meant for the
    visitor: an unexpected error is logged and reported as "something went
    wrong", never with Odoo's traceback.
    """

    def _online(self):
        return request.env["odin.medical.quote.online"].sudo()

    def _answer(self, call):
        try:
            return call()
        except (UserError, ValidationError) as error:
            return {"error": error.args[0]}
        except Exception:
            _logger.exception("Online medical quote failed")
            return {"error": request.env._(
                "Something went wrong on our side. Please try again, or chat with us on WhatsApp.")}

    @http.route("/medical-quote", type="http", auth="public", methods=["GET"], sitemap=False)
    def page(self, lead=None, agent=None, quote_id=None, quote=None, **kwargs):
        props = self._online().page_props(
            lead_token=lead, agent_token=agent, quote_id=quote_id, quote_token=quote)
        return request.render("odin_medical_quote_online.page", {
            "props": props,
            "company": request.env.company.sudo(),
        })

    @http.route("/medical-quote/options", type="jsonrpc", auth="public", methods=["POST"])
    def options(self, family=None, wants=None, tiers=None, only=None):
        return self._answer(lambda: self._online().options(family, wants, tiers, only))

    @http.route("/medical-quote/submit", type="jsonrpc", auth="public", methods=["POST"])
    def submit(self, family=None, wants=None, tiers=None, chosen=None, contact=None, tokens=None):
        return self._answer(
            lambda: self._online().submit(family, wants, tiers, chosen, contact, tokens))

    def _quote(self, comparison_id, token):
        return self._online()._quote_from_token(comparison_id, token)

    @http.route("/medical-quote/q/<int:comparison_id>", type="http", auth="public",
                methods=["GET"], sitemap=False)
    def quote(self, comparison_id, token=None, called=None, **kwargs):
        comparison = self._quote(comparison_id, token)
        if not comparison:
            return request.not_found()
        values = self._online().quote_values(comparison, token)
        values["called"] = bool(called)
        return request.render("odin_medical_quote_online.quote_page", values)

    @http.route("/medical-quote/q/<int:comparison_id>/pdf", type="http", auth="public",
                methods=["GET"], sitemap=False)
    def quote_pdf(self, comparison_id, token=None, **kwargs):
        comparison = self._quote(comparison_id, token)
        if not comparison:
            return request.not_found()
        pdf, _type = request.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "odin_medical_quote.report_medical_quotation", comparison.ids)
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", content_disposition(
                "%s.pdf" % comparison._odin_export_filename())),
        ])

    @http.route("/medical-quote/q/<int:comparison_id>/callback", type="http", auth="public",
                methods=["POST"], sitemap=False)
    def callback(self, comparison_id, token=None, contact_by=None, contact_when=None, **kwargs):
        comparison = self._quote(comparison_id, token)
        if not comparison:
            return request.not_found()
        self._online().request_callback(comparison, contact_by, contact_when)
        return request.redirect("/medical-quote/q/%s?token=%s&called=1#advisor" % (
            comparison.id, token))
