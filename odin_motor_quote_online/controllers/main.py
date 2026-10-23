from odoo import http

from odoo.addons.odin_medical_quote_online.controllers.main import OnlineMedicalQuote

MOTOR = "odin.motor.quote.online"


class OnlineMotorQuote(OnlineMedicalQuote):
    """/motor-quote: the same pages as /medical-quote, for a vehicle."""

    @http.route("/motor-quote", type="http", auth="public", methods=["GET"], sitemap=False)
    def motor_page(self, lead=None, agent=None, quote_id=None, quote=None, **kwargs):
        return self._page(MOTOR, lead, agent, quote_id, quote, hints=kwargs)

    @http.route("/motor-quote/options", type="jsonrpc", auth="public", methods=["POST"])
    def motor_options(self, vehicle=None, extras=None, only=None):
        return self._answer(lambda: self._online(MOTOR).options(vehicle, extras, only))

    @http.route("/motor-quote/submit", type="jsonrpc", auth="public", methods=["POST"])
    def motor_submit(self, vehicle=None, extras=None, chosen=None, contact=None, tokens=None):
        return self._answer(
            lambda: self._online(MOTOR).submit(vehicle, extras, chosen, contact, tokens))

    @http.route("/motor-quote/q/<int:comparison_id>", type="http", auth="public",
                methods=["GET"], sitemap=False)
    def motor_quote(self, comparison_id, token=None, called=None, **kwargs):
        return self._quote_page(comparison_id, token, called)

    @http.route("/motor-quote/q/<int:comparison_id>/pdf", type="http", auth="public",
                methods=["GET"], sitemap=False)
    def motor_quote_pdf(self, comparison_id, token=None, **kwargs):
        return self._quote_pdf(comparison_id, token)

    @http.route("/motor-quote/q/<int:comparison_id>/callback", type="http", auth="public",
                methods=["POST"], sitemap=False)
    def motor_callback(self, comparison_id, token=None, contact_by=None, contact_when=None,
                       **kwargs):
        return self._callback(comparison_id, token, contact_by, contact_when)
