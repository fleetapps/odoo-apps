from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MedicalQuoteCase


@tagged("post_install", "-at_install")
class TestSave(MedicalQuoteCase):
    """From the screen to Contacts, CRM, quotations and the comparison."""

    def afya_selection(self, result, dental=0, **extra):
        afya = self.plan(result, self.afya)
        return dict({
            "template_id": self.afya.id,
            "options": [self.option(afya, "outpatient"), self.option(afya, "dental", dental)],
        }, **extra)

    def jamii_selection(self, result, **extra):
        return dict({"template_id": self.jamii.id, "options": []}, **extra)

    def params(self, plans, **customer):
        return {
            "customer": dict({"source": "new", "name": "Henry", "phone": "0798 726 720"}, **customer),
            "cover": self.cover(),
            "plans": plans,
        }

    # -- totals -------------------------------------------------------------

    def test_totals_are_the_example(self):
        """15,822 + 22,130 + 6,050, levies 0.45% and stamp duty 40."""
        result = self.search()
        totals = self.Quote.compute_totals(self.params([self.afya_selection(result)]))
        afya = totals[str(self.afya.id)]
        self.assertEqual(afya["premium"], 15822 + 22130 + 6050)
        # To the cent it depends on whether the company rounds tax per line.
        self.assertAlmostEqual(afya["taxes"], 44002 * 0.0045, delta=0.015)
        self.assertEqual(afya["charges"], 40)
        self.assertAlmostEqual(afya["total"], afya["premium"] + afya["taxes"] + 40)
        plans = self.env["odin.quote.instalment.plan"].search(
            [("company_id", "=", self.company.id)])
        self.assertEqual([i["months"] for i in afya["instalments"]], plans.mapped("months"))
        for instalment in afya["instalments"]:
            self.assertGreaterEqual(instalment["payable"], afya["total"],
                                    "paying monthly never costs less than paying now")

    def test_discount_percent_and_amount(self):
        result = self.search()
        totals = self.Quote.compute_totals(self.params([
            self.afya_selection(result, discount="10%"),
        ]))[str(self.afya.id)]
        self.assertAlmostEqual(totals["discount"], 4400.2)
        self.assertAlmostEqual(totals["taxes"], 39601.8 * 0.0045, delta=0.015,
                               msg="levies on the premium after discount")
        # An amount comes off the premium, before the levies -- not off the
        # total with tax, as Odoo's own fixed discount would.
        totals = self.Quote.compute_totals(self.params([
            self.afya_selection(result, discount="1,000", policy_charge="500"),
        ]))[str(self.afya.id)]
        self.assertAlmostEqual(totals["discount"], 1000)
        self.assertAlmostEqual(totals["untaxed"], 44002 - 1000 + 40 + 500)
        self.assertEqual(totals["policy_charge"], 500)
        bad = self.Quote.compute_totals(self.params([
            self.afya_selection(result, discount="ten")]))[str(self.afya.id)]
        self.assertIn("error", bad)
        too_much = self.Quote.compute_totals(self.params([
            self.afya_selection(result, discount="99999")]))[str(self.afya.id)]
        self.assertIn("more than the premium", too_much["error"])

    def test_dental_needs_outpatient(self):
        afya = self.plan(self.search(), self.afya)
        with self.assertRaisesRegex(UserError, "only sold together with Outpatient"):
            self.Quote.save_quote(self.params([{
                "template_id": self.afya.id, "options": [self.option(afya, "dental")]}]))

    # -- saving -------------------------------------------------------------

    def test_new_prospect(self):
        result = self.search()
        params = self.params([
            self.afya_selection(result, discount="10%"),
            self.jamii_selection(result, recommended=True, policy_charge="500"),
        ])
        preview = self.Quote.compute_totals(params)
        saved = self.Quote.save_quote(params)

        partner = self.env["res.partner"].browse(saved["partner"]["id"])
        self.assertEqual(partner.name, "Henry")
        self.assertEqual(partner.phone, "+254798726720", "stored the way existing contacts are")
        lead = self.env["crm.lead"].browse(saved["lead"]["id"])
        self.assertEqual(lead.type, "opportunity")
        self.assertEqual(lead.partner_id, partner)
        self.assertEqual(lead.user_id, self.agent)

        comparison = self.env["odin.quote.comparison"].browse(saved["comparison"]["id"])
        self.assertEqual(comparison.opportunity_id, lead)
        self.assertTrue(comparison.odin_from_builder)
        orders = comparison.plan_ids.sorted("sequence").order_id
        self.assertEqual(orders.sale_order_template_id, self.afya | self.jamii)
        self.assertEqual(lead.order_ids, orders)
        for order in orders:
            self.assertEqual(order.partner_id, partner)
            self.assertAlmostEqual(
                order.amount_total, preview[str(order.sale_order_template_id.id)]["total"],
                msg="the quotation is what the screen showed")
        self.assertEqual(comparison.plan_ids.filtered("highlight").order_id.sale_order_template_id, self.jamii)
        jamii_order = orders.filtered(lambda o: o.sale_order_template_id == self.jamii)
        self.assertEqual(lead.expected_revenue, jamii_order.amount_untaxed, "the recommended plan")

        afya_order = orders - jamii_order
        lines = afya_order.order_line.filtered(lambda l: not l.display_type)
        self.assertEqual(lines.product_id, self.afya_ip[4] | self.afya_op[4] | self.afya_dental[0]
                         | self.stamp | self.env.ref("odin_medical_quote.product_discount"))
        dental = lines.filtered(lambda l: l.product_id == self.afya_dental[0])
        self.assertEqual(dental.product_uom_qty, 5)
        sections = afya_order.order_line.filtered(lambda l: l.display_type == "line_section")
        self.assertEqual(
            {s.benefit_key: s.cover_limit for s in sections if s.cover_limit},
            {"inpatient": 200000, "outpatient": 50000, "dental": 5000},
            "the tier picked is the section's limit, for the client reports")
        self.assertFalse(afya_order.order_line.filtered("is_optional"),
                         "priced for this family: not for the client to change on the portal")
        self.assertEqual(comparison.member_ids.mapped("role"),
                         ["principal", "spouse", "child", "child", "child"])

        # The comparison report reads the same quotations: Afya first, as picked.
        premiums = {row["label"]: row["cells"] for row in comparison._matrix()["rows"]
                    if row["kind"] == "premium"}
        self.assertEqual(premiums["Inpatient"], [15822, 21286 + 17289 + 3 * 9033])
        self.assertAlmostEqual(premiums["Discount"][0], -4400.2)
        self.assertEqual(premiums["Policy charge"], [0, 500])

    def test_existing_customer_and_lead(self):
        customer = self.env["res.partner"].create({"name": "Existing Client", "phone": "+254700000001"})
        result = self.search()
        saved = self.Quote.save_quote(self.params(
            [self.jamii_selection(result)], source="existing", partner_id=customer.id))
        self.assertEqual(saved["partner"]["id"], customer.id)
        self.assertEqual(self.env["crm.lead"].browse(saved["lead"]["id"]).partner_id, customer)

        lead = self.env["crm.lead"].create({
            "name": "WhatsApp — Jane", "type": "lead", "contact_name": "Jane Achieng",
            "phone": "+254711000222",
        })
        saved = self.Quote.save_quote(self.params(
            [self.jamii_selection(result)], source="lead", lead_id=lead.id))
        self.assertEqual(saved["lead"]["id"], lead.id)
        self.assertEqual(lead.type, "opportunity", "converted, as Convert to opportunity would")
        self.assertEqual(lead.partner_id.name, "Jane Achieng")
        self.assertEqual(lead.order_ids.partner_id, lead.partner_id)

    def test_matches_and_lookups(self):
        customer = self.env["res.partner"].create({"name": "Henry Otieno", "phone": "+254798726720"})
        matches = self.Quote.match_prospect("Henry", "0798726720", "")
        self.assertEqual([m["id"] for m in matches], [customer.id], "07... finds +2547...")
        self.assertEqual([c["id"] for c in self.Quote.find_contacts("Otieno")], [customer.id])

    def test_resave_updates_in_place(self):
        result = self.search()
        saved = self.Quote.save_quote(self.params([
            self.afya_selection(result), self.jamii_selection(result)]))
        comparison = self.env["odin.quote.comparison"].browse(saved["comparison"]["id"])
        afya_order = comparison.plan_ids.order_id.filtered(
            lambda o: o.sale_order_template_id == self.afya)
        jamii_order = comparison.plan_ids.order_id - afya_order

        params = dict(comparison.odin_builder_params, comparison_id=comparison.id)
        params["plans"] = [self.afya_selection(result, dental=1)]
        self.Quote.save_quote(params)
        self.assertEqual(comparison.plan_ids.order_id, afya_order, "same quotation, rebuilt")
        self.assertEqual(jamii_order.state, "cancel", "a plan taken off is cancelled")
        self.assertIn(self.afya_dental[1], afya_order.order_line.product_id)
        self.assertNotIn(self.afya_dental[0], afya_order.order_line.product_id)
        self.assertEqual(len(self.env["crm.lead"].search([("partner_id.name", "=", "Henry")])), 1,
                         "no second opportunity")

    def test_option_keys_survive_template_edits(self):
        """A saved quote reopens on the same options after the rate card's
        lines are reordered or rewritten: keys name the benefit and the
        rate, not a template line."""
        result = self.search()
        before = self.option(self.plan(result, self.afya), "outpatient")
        self.assertTrue(before.startswith("outpatient/"), before)
        lines = self.afya.sale_order_template_line_ids
        self.afya.sale_order_template_line_ids = [Command.clear()] + [Command.create({
            "sequence": line.sequence, "display_type": line.display_type, "name": line.name,
            "product_id": line.product_id.id, "product_uom_qty": line.product_uom_qty,
            "benefit_label": line.benefit_label, "benefit_key": line.benefit_key,
            "cover_limit": line.cover_limit, "is_optional": line.is_optional,
        }) for line in lines]
        self.assertEqual(self.option(self.plan(self.search(), self.afya), "outpatient"), before)

    def test_resave_keeps_quotations_added_by_hand(self):
        result = self.search()
        saved = self.Quote.save_quote(self.params([self.jamii_selection(result)]))
        comparison = self.env["odin.quote.comparison"].browse(saved["comparison"]["id"])
        by_hand = self.env["sale.order"].create({
            "partner_id": comparison.partner_id.id, "sale_order_template_id": self.afya70.id})
        comparison.plan_ids = [Command.create({"order_id": by_hand.id, "sequence": 99})]
        params = dict(comparison.odin_builder_params, comparison_id=comparison.id)
        self.Quote.save_quote(params)
        self.assertEqual(by_hand.state, "draft", "not the screen's to cancel")
        self.assertIn(by_hand, comparison.plan_ids.order_id)

    def test_follow_up(self):
        result = self.search()
        saved = self.Quote.save_quote(self.params([self.jamii_selection(result)]))
        comparison = self.env["odin.quote.comparison"].browse(saved["comparison"]["id"])
        lead = comparison.opportunity_id
        self.assertEqual(lead.date_deadline, comparison.validity_date)
        self.assertEqual(len(lead.activity_ids), 1)
        self.assertEqual(lead.activity_ids.user_id, self.agent)
        self.assertIn(comparison.name, lead.activity_ids.summary)
        self.Quote.save_quote(dict(comparison.odin_builder_params, comparison_id=comparison.id))
        self.assertEqual(len(lead.activity_ids), 1, "saving again does not add another")

    def test_existing_customer_open_opportunities(self):
        company = self.env["res.partner"].create({"name": "Acme Ltd", "is_company": True})
        person = self.env["res.partner"].create({"name": "Jane", "parent_id": company.id})
        lead = self.env["crm.lead"].create({
            "name": "Staff cover", "type": "opportunity", "partner_id": person.id})
        self.env["crm.lead"].create({
            "name": "Old sale", "type": "opportunity", "partner_id": company.id,
        }).action_set_won()
        self.assertEqual([found["id"] for found in self.Quote.partner_leads(company.id)], [lead.id])
        self.assertEqual(self.Quote.partner_leads(0), [])

    def test_unassigned_lead_goes_to_the_agent(self):
        lead = self.env["crm.lead"].create({
            "name": "Website", "type": "lead", "contact_name": "Jane Achieng",
            "phone": "+254711000222", "user_id": False,
        })
        self.Quote.save_quote(self.params(
            [self.jamii_selection(self.search())], source="lead", lead_id=lead.id))
        self.assertEqual(lead.user_id, self.agent)

    def test_validation(self):
        result = self.search()
        with self.assertRaisesRegex(UserError, "name"):
            self.Quote.save_quote(self.params([self.jamii_selection(result)], name=""))
        with self.assertRaisesRegex(UserError, "phone number or an email"):
            self.Quote.save_quote(self.params([self.jamii_selection(result)], phone=""))
        with self.assertRaisesRegex(UserError, "at least one plan"):
            self.Quote.save_quote(self.params([]))
        with self.assertRaisesRegex(UserError, "Choose the customer"):
            self.Quote.save_quote(self.params([self.jamii_selection(result)], source="existing"))
