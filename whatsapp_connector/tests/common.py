from odoo import Command
from odoo.tests import TransactionCase, new_test_user


class WhatsappCase(TransactionCase):
    """Accounts, users and a helper to open a WhatsApp conversation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.user_a = new_test_user(
            cls.env, login="wa_andrew", name="Andrew",
            groups="base.group_user,whatsapp_connector.group_whatsapp_user,sales_team.group_sale_salesman",
        )
        cls.user_b = new_test_user(
            cls.env, login="wa_sarah", name="Sarah",
            groups="base.group_user,whatsapp_connector.group_whatsapp_user,sales_team.group_sale_salesman",
        )
        cls.user_c = new_test_user(
            cls.env, login="wa_brian", name="Brian",
            groups="base.group_user,whatsapp_connector.group_whatsapp_user,sales_team.group_sale_salesman",
        )
        cls.manager = new_test_user(
            cls.env, login="wa_manager", name="Manager",
            groups="base.group_user,whatsapp_connector.group_whatsapp_manager,sales_team.group_sale_manager",
        )
        cls.outsider = new_test_user(cls.env, login="wa_outsider", name="Outsider", groups="base.group_user")
        cls.account = cls.env["whatsapp_connector.account"].create({
            "name": "Sales",
            "waba_id": "102290129340398",
            "phone_number_id": "106540352242922",
            "app_id": "1234567890",
            "app_secret": "test-app-secret",
            "access_token": "test-access-token",
            "api_version": "v25.0",
            "operator_ids": [
                Command.create({"user_id": cls.user_a.id, "sequence": 1}),
                Command.create({"user_id": cls.user_b.id, "sequence": 2}),
                Command.create({"user_id": cls.user_c.id, "sequence": 3}),
            ],
        })
        cls.customer = cls.env["res.partner"].create({
            "name": "Sheena Nelson",
            "phone": "+16505551234",
            "wa_bsuid": "US.13491208655302741918",
        })

    @classmethod
    def _make_channel(cls, partners, key="US.13491208655302741918", **extra):
        vals = {
            "name": "Sheena Nelson",
            "channel_type": "whatsapp",
            "wa_account_id": cls.account.id,
            "wa_customer_key": key,
            "wa_bsuid": key,
            "wa_partner_id": cls.customer.id,
            "channel_member_ids": [Command.create({"partner_id": p.id}) for p in partners],
        }
        vals.update(extra)
        return cls.env["discuss.channel"].create(vals)
