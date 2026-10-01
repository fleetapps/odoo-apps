from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .test_outbound import PHONE, OutboundCase, sent
from odoo.addons.mail.tools.discuss import Store
from odoo.addons.whatsapp_connector.tools.meta_api import WhatsAppApi

# Meta's documented example (OpenAPI v23.0, "Create template w/ image header...",
# with a text header instead of the image)
META_TEMPLATE = {
    "id": "1689556908129832",
    "name": "limited_time_offer",
    "language": "en_US",
    "status": "APPROVED",
    "category": "MARKETING",
    "components": [
        {"type": "HEADER", "format": "TEXT", "text": "Our {{1}} is on!",
         "example": {"header_text": ["Summer Sale"]}},
        {"type": "BODY", "text": "Hi {{1}}! Get our {{2}} for as low as {{3}}.",
         "example": {"body_text": [["Mark", "Tuscan Getaway package", "800"]]}},
        {"type": "FOOTER", "text": "Offer valid until May 31, 2023"},
        {"type": "BUTTONS", "buttons": [
            {"type": "PHONE_NUMBER", "text": "Call", "phone_number": "16467043595"},
            {"type": "URL", "text": "Shop Now", "url": "https://shop.example.com/shop?promo={{1}}",
             "example": ["summer2023"]},
        ]},
    ],
}


def meta_template(**changes):
    data = dict(META_TEMPLATE, components=[dict(c) for c in META_TEMPLATE["components"]])
    data.update(changes)
    return data


@tagged("post_install", "-at_install")
class TestTemplateSync(OutboundCase):
    """R25: templates synced from Meta are ready to send."""

    def _sync(self, *templates):
        with patch.object(WhatsAppApi, "get_templates", return_value=list(templates)):
            self.account.action_sync_templates()
        return self.env["whatsapp_connector.template"].search([("template_name", "=", "limited_time_offer")])

    def test_sync_creates_variables_with_meta_examples(self):
        template = self._sync(meta_template())
        self.assertEqual(template.button_ids.mapped("button_type"), ["phone_number", "url"])
        self.assertEqual(template.button_ids[1].url_type, "dynamic")
        variables = {(v.line_type, v.placeholder_index): v for v in template.variable_ids}
        self.assertEqual(sorted(variables), [("body", 1), ("body", 2), ("body", 3), ("button", 1), ("header", 1)])
        self.assertEqual(variables["header", 1].demo_value, "Summer Sale")
        self.assertEqual(variables["body", 2].demo_value, "Tuscan Getaway package")
        self.assertEqual(variables["button", 1].button_id, template.button_ids[1])
        self.assertEqual(variables["button", 1].demo_value, "summer2023")
        self.assertEqual(set(template.variable_ids.mapped("field_type")), {"free_text"})

    def test_resync_keeps_configuration(self):
        template = self._sync(meta_template())
        body_1 = template.variable_ids.filtered(lambda v: v.line_type == "body" and v.placeholder_index == 1)
        body_1.write({"field_type": "field", "field_name": "name"})
        button = template.button_ids[1]
        # Meta drops {{3}} from the body
        data = meta_template()
        data["components"][1] = {"type": "BODY", "text": "Hi {{1}}! Get our {{2}}.",
                                  "example": {"body_text": [["Mark", "Tuscan"]]}}
        self._sync(data)
        self.assertTrue(body_1.exists())
        self.assertEqual(body_1.field_type, "field", "configured variables survive a sync")
        self.assertEqual(template.button_ids[1], button, "buttons are updated in place")
        self.assertFalse(template.variable_ids.filtered(lambda v: v.placeholder_index == 3))

    def test_sync_one_template(self):
        template = self._sync(meta_template(status="PENDING"))
        with patch.object(WhatsAppApi, "get_template", return_value=meta_template(status="APPROVED")) as get:
            template.action_sync_template()
        get.assert_called_once_with("1689556908129832")
        self.assertEqual(template.status, "APPROVED")

    def test_send_synced_template_with_url_button(self):
        template = self._sync(meta_template())
        template.model_id = self.env["ir.model"]._get_id("res.partner")
        template.with_user(self.user_a)._wa_send_to_record(self.customer)
        send = self._send_queued()
        payload = send.call_args.args[0]["template"]
        self.assertEqual(payload["components"], [
            {"type": "header", "parameters": [{"type": "text", "text": "Summer Sale"}]},
            {"type": "body", "parameters": [
                {"type": "text", "text": "Mark"}, {"type": "text", "text": "Tuscan Getaway package"},
                {"type": "text", "text": "800"},
            ]},
            {"type": "button", "sub_type": "url", "index": "1",
             "parameters": [{"type": "text", "text": "summer2023"}]},
        ])

    def test_header_file_required(self):
        data = meta_template()
        data["components"][0] = {"type": "HEADER", "format": "IMAGE"}
        template = self._sync(data)
        template.model_id = self.env["ir.model"]._get_id("res.partner")
        with self.assertRaisesRegex(UserError, "header file"):
            template.with_user(self.user_a)._wa_send_to_record(self.customer)

    def test_missing_variable_refused_before_sending(self):
        template = self._sync(meta_template())
        template.model_id = self.env["ir.model"]._get_id("res.partner")
        template.variable_ids.filtered(lambda v: v.line_type == "body" and v.placeholder_index == 2).unlink()
        with self.assertRaisesRegex(UserError, r"no variable for \{\{2\}\} of its body"):
            template.with_user(self.user_a)._wa_send_to_record(self.customer)


@tagged("post_install", "-at_install")
class TestTemplateSubmit(OutboundCase):
    """R25: templates written in Odoo are submitted to Meta for approval."""

    def _template(self, **values):
        return self.env["whatsapp_connector.template"].create({
            "name": "Order update",
            "template_name": "order_update",
            "language_code": "en_US",
            "account_id": self.account.id,
            "category": "utility",
            "header_type": "text",
            "header_text": "Order {{1}}",
            "body": "Hi {{1}}, your order is on its way.",
            "footer": "Thank you",
            "button_ids": [
                Command.create({"sequence": 1, "button_type": "quick_reply", "text": "Stop updates"}),
                Command.create({"sequence": 2, "button_type": "url", "text": "Track",
                                "url_type": "dynamic", "website_url": "https://shop.example.com/t/{{1}}"}),
                Command.create({"sequence": 3, "button_type": "phone_number", "text": "Call",
                                "call_number": "+16467043595"}),
            ],
            "variable_ids": [
                Command.create({"line_type": "header", "placeholder_index": 1, "demo_value": "S0042"}),
                Command.create({"line_type": "body", "placeholder_index": 1, "demo_value": "Mark"}),
            ],
            **values,
        })

    def test_submit_new_template(self):
        template = self._template()
        url_button = template.button_ids.filtered(lambda b: b.button_type == "url")
        template.variable_ids = [Command.create({
            "line_type": "button", "button_id": url_button.id, "demo_value": "ABC123",
        })]
        answer = {"id": "1473688840035974", "status": "PENDING", "category": "UTILITY"}
        with patch.object(WhatsAppApi, "submit_template", return_value=answer) as submit:
            template.action_submit_template()
        self.assertEqual(submit.call_args.args[0], {
            "name": "order_update",
            "language": "en_US",
            "category": "UTILITY",
            "components": [
                {"type": "HEADER", "format": "TEXT", "text": "Order {{1}}",
                 "example": {"header_text": ["S0042"]}},
                {"type": "BODY", "text": "Hi {{1}}, your order is on its way.",
                 "example": {"body_text": [["Mark"]]}},
                {"type": "FOOTER", "text": "Thank you"},
                {"type": "BUTTONS", "buttons": [
                    {"type": "QUICK_REPLY", "text": "Stop updates"},
                    {"type": "URL", "text": "Track", "url": "https://shop.example.com/t/{{1}}",
                     "example": ["ABC123"]},
                    {"type": "PHONE_NUMBER", "text": "Call", "phone_number": "+16467043595"},
                ]},
            ],
        })
        self.assertRecordValues(template, [{"meta_template_id": "1473688840035974", "status": "PENDING"}])

    def test_submit_changes_rereads_status(self):
        template = self._template(meta_template_id="555", status="APPROVED")
        url_button = template.button_ids.filtered(lambda b: b.button_type == "url")
        template.variable_ids = [Command.create({
            "line_type": "button", "button_id": url_button.id, "demo_value": "ABC123",
        })]
        back = {"id": "555", "name": "order_update", "language": "en_US", "status": "PENDING",
                "category": "UTILITY", "components": [{"type": "BODY", "text": "Hi {{1}}, your order is on its way."}]}
        with patch.object(WhatsAppApi, "edit_template", return_value={"success": True}) as edit, \
                patch.object(WhatsAppApi, "get_template", return_value=back):
            template.action_submit_template()
        self.assertEqual(edit.call_args.args[0], "555")
        self.assertEqual(template.status, "PENDING")

    def test_submit_refusals(self):
        template = self._template()
        with self.assertRaisesRegex(UserError, r"sample value for \{\{1\}\} of the button Track"):
            template._wa_meta_components()
        media = self._template(template_name="with_image", header_type="image", header_text=False)
        with self.assertRaisesRegex(UserError, "WhatsApp Manager"):
            media.action_submit_template()


@tagged("post_install", "-at_install")
class TestComposer(OutboundCase):
    """R10/R25: the Send WhatsApp Message wizard, for one record, several, or a conversation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Template = cls.env["whatsapp_connector.template"]
        common = {"account_id": cls.account.id, "status": "APPROVED", "language_code": "en_US"}
        cls.lead_template = Template.create({
            **common, "name": "Quote follow-up", "template_name": "quote_followup",
            "model_id": cls.env["ir.model"]._get_id("crm.lead"),
            "body": "Hello {{1}}, {{2}}",
            "variable_ids": [
                Command.create({"line_type": "body", "placeholder_index": 1,
                                "field_type": "field", "field_name": "partner_id.name"}),
                Command.create({"line_type": "body", "placeholder_index": 2,
                                "field_type": "free_text", "demo_value": "any questions?"}),
            ],
        })
        cls.partner_template = Template.create({
            **common, "name": "Reconnect", "template_name": "reconnect",
            "model_id": cls.env["ir.model"]._get_id("res.partner"),
            "body": "Hi {{1}}, can we continue?",
            "variable_ids": [Command.create({"line_type": "body", "placeholder_index": 1,
                                             "field_type": "field", "field_name": "name"})],
        })
        cls.pending = Template.create({
            **common, "status": "PENDING", "name": "Pending", "template_name": "pending",
            "model_id": cls.env["ir.model"]._get_id("crm.lead"), "body": "x",
        })
        cls.lead = cls.env["crm.lead"].create({
            "name": "Sofa", "partner_id": cls.customer.id, "user_id": cls.user_a.id,
        })

    def _form(self, user, **context):
        Composer = self.env["whatsapp_connector.composer"].with_user(user).with_context(**context)
        return Form(Composer)

    def test_single_record(self):
        form = self._form(self.user_a, active_model="crm.lead", active_id=self.lead.id)
        self.assertEqual(form.available_template_ids[:], self.lead_template, "approved templates of the model")
        form.template_id = self.lead_template
        self.assertEqual(form.preview, "Hello Sheena Nelson, any questions?")
        with form.free_text_ids.edit(0) as line:
            line.value = "shall we book the delivery?"
        self.assertEqual(form.preview, "Hello Sheena Nelson, shall we book the delivery?")
        composer = form.save()
        composer.action_send()
        wa = self.env["whatsapp_connector.message"].search([("template_id", "=", self.lead_template.id)])
        self.assertIn("shall we book the delivery?", wa.body)
        self._send_queued()
        self.assertTrue(wa.external_message_id)

    def test_template_restricted_to_users(self):
        self.lead_template.user_ids = self.user_b
        form = self._form(self.user_a, active_model="crm.lead", active_id=self.lead.id)
        self.assertFalse(form.available_template_ids[:])

    def test_several_records_skips_unreachable(self):
        no_phone = self.env["res.partner"].create({"name": "No Phone"})
        other = self.env["crm.lead"].create({"name": "Chair", "partner_id": no_phone.id, "user_id": self.user_a.id})
        form = self._form(self.user_a, active_model="crm.lead", active_ids=[self.lead.id, other.id])
        form.template_id = self.lead_template
        action = form.save().action_send()
        self.assertIn("1 of 2", action["params"]["message"])
        self.assertIn("No Phone", action["params"]["message"])
        self.assertEqual(self.env["whatsapp_connector.message"].search_count(
            [("template_id", "=", self.lead_template.id)]), 1)

    def test_from_conversation(self):
        """In Discuss, a template goes to that conversation, even outside the 24-hour window."""
        channel = self._open_channel(last_customer_message=fields.Datetime.now() - timedelta(days=3))
        form = self._form(self.user_a, active_model="discuss.channel", active_id=channel.id)
        self.assertEqual(form.available_template_ids[:], self.partner_template)
        form.template_id = self.partner_template
        form.save().action_send()
        wa = channel.wa_message_ids.filtered(lambda m: m.message_type == "template")
        self.assertEqual(len(wa), 1)
        send = self._send_queued(answer=sent("wamid.t"))
        self.assertEqual(send.call_args.args[0]["to"], PHONE)

    def test_chatter_flag(self):
        """The chatter shows its WhatsApp button when the model has templates for the user (R10)."""
        def flag(record, user):
            store = Store()
            record.with_user(user)._thread_to_store(store, [], request_list=["wa_can_send"])
            return store.get_result()["mail.thread"][0].get("wa_can_send")

        self.assertTrue(flag(self.lead, self.user_a))
        self.lead_template.user_ids = self.user_b
        self.assertFalse(flag(self.lead, self.user_a))
        self.assertFalse(flag(self.lead, self.outsider), "not a WhatsApp user")


@tagged("post_install", "-at_install")
class TestServerAction(OutboundCase):
    """R11: "Send WhatsApp" server actions, for automation rules."""

    def test_send_whatsapp_action(self):
        template = self.env["whatsapp_connector.template"].create({
            "name": "Welcome", "template_name": "welcome", "account_id": self.account.id,
            "status": "APPROVED", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "body": "Welcome!",
        })
        action = self.env["ir.actions.server"].create({
            "name": "Welcome on WhatsApp",
            "model_id": self.env["ir.model"]._get_id("res.partner"),
            "state": "whatsapp",
            "wa_template_id": template.id,
        })
        no_phone = self.env["res.partner"].create({"name": "No Phone"})
        action.with_context(active_model="res.partner", active_ids=[self.customer.id, no_phone.id]).run()
        wa = self.env["whatsapp_connector.message"].search([("template_id", "=", template.id)])
        self.assertEqual(wa.channel_id.wa_partner_id, self.customer, "the unreachable contact is skipped")
        # the template is reset when the action's model changes
        action.model_id = self.env["ir.model"]._get_id("crm.lead")
        self.assertFalse(action.wa_template_id)
