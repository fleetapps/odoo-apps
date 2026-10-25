# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Who sees what: outlet staff their tills, officers everything, nobody the secrets."""

import base64
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import MpesaCommon


@tagged("post_install", "-at_install")
class TestSecurity(MpesaCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cashier = new_test_user(
            cls.env, login="cashier", groups="base.group_user,x_ke_mpesa.group_mpesa_outlet",
            company_id=cls.company.id, company_ids=[cls.company.id])
        cls.officer = new_test_user(
            cls.env, login="mpesa_officer",
            groups="base.group_user,x_ke_mpesa.group_mpesa_officer",
            company_id=cls.company.id, company_ids=[cls.company.id])
        cls.till.user_ids = cls.cashier

    def setUp(self):
        super().setUp()
        self.mine = self.fake.c2b_payment("600100", 100)
        self.theirs = self.fake.c2b_payment("600101", 200)
        self.deliver()

    def test_outlet_staff_see_their_till_only(self):
        visible = self.env["mpesa.transaction"].with_user(self.cashier).search([])
        self.assertEqual(visible.mapped("receipt"), [self.mine])
        self.assertEqual(self.env["mpesa.shortcode"].with_user(self.cashier).search([]), self.till)

    def test_outlet_staff_have_no_accounting(self):
        self.assertFalse(self.cashier.has_group("account.group_account_invoice"))
        self.assertFalse(self.cashier.has_group("account.group_account_readonly"))
        with self.assertRaises(AccessError):
            self.env["account.bank.statement.line"].with_user(self.cashier).search([])

    def test_officer_sees_all(self):
        visible = self.env["mpesa.transaction"].with_user(self.officer).search([])
        self.assertEqual(set(visible.mapped("receipt")), {self.mine, self.theirs})

    def test_secrets_are_for_managers(self):
        app = self.app.with_user(self.officer)
        with self.assertRaises(AccessError):
            app.read(["consumer_secret"])
        with self.assertRaises(AccessError):
            self.store.with_user(self.officer).read(["passkey"])
        with self.assertRaises(AccessError):
            self.till.with_user(self.officer).read(["security_credential"])
        with self.assertRaises(AccessError):
            app.read(["access_token"])

    def test_outlet_staff_cannot_write_receipts(self):
        tx = self.transactions(receipt=self.mine).with_user(self.cashier)
        with self.assertRaises(AccessError):
            tx.write({"amount": 1})

    def test_other_company(self):
        other = self.setup_other_company(name="Synad")["company"]
        stranger = new_test_user(self.env, login="synad_officer",
                                 groups="base.group_user,x_ke_mpesa.group_mpesa_officer",
                                 company_id=other.id, company_ids=[other.id])
        self.assertFalse(self.env["mpesa.transaction"].with_user(stranger).search([]))
        self.assertFalse(self.env["mpesa.shortcode"].with_user(stranger).search([]))

    def test_initiator_password_is_never_stored(self):
        """Encrypted with the app's certificate on save; only the ciphertext stays."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sandbox.test")])
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(key.public_key()).serial_number(1)
                       .not_valid_before(datetime(2020, 1, 1))
                       .not_valid_after(datetime.now() + timedelta(days=1))
                       .sign(key, hashes.SHA256()))
        self.app.certificate = certificate.public_bytes(serialization.Encoding.PEM).decode()
        self.till.initiator_password = "Safaricom999!*!"
        credential = self.till.sudo().security_credential
        self.assertTrue(credential)
        self.assertNotIn("Safaricom999", credential)
        plain = key.decrypt(base64.b64decode(credential), padding.PKCS1v15())
        self.assertEqual(plain, b"Safaricom999!*!")
        self.assertFalse(self.till.initiator_password)
