# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

import json
from urllib.parse import urlsplit

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..controllers.main import KIND_BY_PATH
from .fake_daraja import SANDBOX_PASSKEY, FakeDaraja

SAFARICOM_IP = "196.201.214.10"
PHONE = "0708374149"


class MpesaCommon(AccountTestInvoicingCommon):
    """A Kenyan company with a store, a till under it and a paybill, all on a
    fake Daraja, with their journals set up and C2B URLs registered."""

    country_code = "KE"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Jobs run inline: a callback is processed inside its own _receive().
        cls.env = cls.env(context=dict(cls.env.context, queue_job__no_delay=True))
        cls.fake = FakeDaraja().start()
        cls.addClassCleanup(cls.fake.stop)
        cls.company = cls.company_data["company"]
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://pay.example.co.ke")
        cls.app = cls.env["mpesa.app"].create({
            "name": "Fake Daraja",
            "company_id": cls.company.id,
            "environment": "sandbox",
            "consumer_key": cls.fake.consumer[0],
            "consumer_secret": cls.fake.consumer[1],
            "api_url": cls.fake.url,
        })
        Shortcode = cls.env["mpesa.shortcode"]
        cls.store = Shortcode.create({
            "kind": "store", "number": "174379", "company_id": cls.company.id,
            "app_id": cls.app.id, "passkey": SANDBOX_PASSKEY,
        })
        cls.till = Shortcode.create({
            "kind": "till", "number": "600100", "parent_id": cls.store.id,
            "company_id": cls.company.id, "app_id": cls.app.id,
        })
        cls.till_b = Shortcode.create({
            "kind": "till", "number": "600101", "parent_id": cls.store.id,
            "company_id": cls.company.id, "app_id": cls.app.id,
        })
        cls.paybill = Shortcode.create({
            "kind": "paybill", "number": "600200", "company_id": cls.company.id,
            "app_id": cls.app.id, "passkey": "paybillpasskey",
        })
        cls.fake.passkeys["600200"] = "paybillpasskey"
        (cls.store | cls.till | cls.till_b | cls.paybill).action_setup_accounting()
        (cls.till | cls.till_b | cls.paybill).action_register_c2b()
        cls.partner_a.phone = PHONE

    def setUp(self):
        super().setUp()
        urls = dict(self.fake.c2b_urls)
        self.fake.reset()
        self.fake.c2b_urls.update(urls)

    # -- helpers ----------------------------------------------------------

    def send(self, url, body, ip=SAFARICOM_IP):
        """What the controller does with a callback, without HTTP."""
        path = urlsplit(url).path
        _empty, _pay, _ke, token, subpath = path.split("/", 4)
        return self.env["mpesa.callback"].sudo()._receive(
            KIND_BY_PATH[subpath], token, json.dumps(body).encode(), ip)

    def deliver(self, **kwargs):
        return self.fake.deliver(self.send, **kwargs)

    def invoice(self, amount, partner=None, post=True):
        return self.init_invoice("out_invoice", partner=partner or self.partner_a,
                                 amounts=[amount], taxes=[], post=post)

    def transactions(self, **domain):
        return self.env["mpesa.transaction"].search(
            [(key, "=", value) for key, value in domain.items()])

    def stk(self, amount, shortcode=None, **kwargs):
        return self.env["mpesa.transaction"]._stk_request(
            shortcode or self.till, amount, kwargs.pop("phone", PHONE),
            reference=kwargs.pop("reference", "TEST"), **kwargs)
