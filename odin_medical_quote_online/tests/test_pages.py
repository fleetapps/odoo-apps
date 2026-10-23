import re

from odoo.tests import HttpCase, tagged

from .test_online import OnlineCase


@tagged("post_install", "-at_install")
class TestOnlinePages(OnlineCase, HttpCase):
    """The pages as a visitor gets them: no login, nothing but the link."""

    def test_form_page(self):
        response = self.url_open("/medical-quote")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="odin_medical_quote_online.OnlineQuote"', response.text)
        self.assertIn("noindex", response.text)

    def test_prices_over_json(self):
        result = self.make_jsonrpc_request("/medical-quote/options", {"family": self.family})
        self.assertEqual({p["id"] for p in result["plans"]}, {self.afya.id, self.jamii.id})
        problem = self.make_jsonrpc_request("/medical-quote/options", {"family": {"who": "me"}})
        self.assertEqual(problem, {"error": "Enter your age."},
                         "a sentence for the visitor, not a traceback")

    def test_quote_page(self):
        result, comparison = self.submit()
        path = "/medical-quote" + result["url"].split("/medical-quote", 1)[1]
        token = path.split("token=")[1]

        self.assertEqual(self.url_open("/medical-quote/q/%s?token=wrong" % comparison.id).status_code, 404)
        self.assertEqual(self.url_open("/medical-quote/q/%s" % comparison.id).status_code, 404)

        page = self.url_open(path)
        self.assertEqual(page.status_code, 200)
        for text in ("Hi Grace, here is your quote", comparison.name, "Plan 1 (KES 200,000)",
                     "Hospital stays", "Doctor visits &amp; medicines", "Talk to an advisor"):
            self.assertIn(text, page.text)
        self.assertNotIn("Below 70", page.text)

        pdf = self.url_open("/medical-quote/q/%s/pdf?token=%s" % (comparison.id, token))
        self.assertEqual(pdf.status_code, 200)
        self.assertIn("Medical%20quotation%20-%20Grace%20Wanjiru", pdf.headers["Content-Disposition"])
        self.assertEqual(self.url_open("/medical-quote/q/%s/pdf?token=x" % comparison.id).status_code, 404)

        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        called = self.url_open("/medical-quote/q/%s/callback" % comparison.id, data={
            "csrf_token": csrf, "token": token, "contact_by": "call", "contact_when": "evening"})
        self.assertEqual(called.status_code, 200)
        self.assertIn("Thank you", called.text)
        self.assertEqual(comparison.odin_contact_when, "evening")
        self.assertTrue(comparison.odin_callback_requested)

    def test_ad_link_source(self):
        """An ad's link: the source and campaign reach the opportunity through
        the cookies the page sets, as for any Odoo web form."""
        self.company.odin_online_user_id = False
        self.url_open("/medical-quote?utm_source=Facebook&utm_campaign=Family%20October")
        result = self.make_jsonrpc_request("/medical-quote/submit", {
            "family": self.family, "chosen": [self.afya.id], "contact": self.contact(),
            "tokens": {}})
        self.assertIn("/medical-quote/q/", result["url"])
        comparison = self.Comparison.search([("odin_channel", "=", "online")], order="id desc", limit=1)
        self.assertEqual(comparison.opportunity_id.source_id.name, "Facebook")
        self.assertEqual(comparison.odin_campaign_id.name, "Family October")
        self.assertFalse(comparison.opportunity_id.user_id, "for the team")
