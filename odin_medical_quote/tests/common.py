from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests import TransactionCase, new_test_user


class MedicalQuoteCase(TransactionCase):
    """A small rate card in the shape of the real ones.

    *Afya* is priced per family by family size (Member Only to Member + 5),
    in a below-70 and an above-70 variant, with outpatient per family and
    dental per person that may only be bought with outpatient.

    *Jamii* is priced per person by role and age band, with outpatient per
    person by band, a Hosi Cash rider for the main member and each dependant,
    and a children-only plan.

    Prices are the real rate card's, so the figures can be checked by hand.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # Phone numbers are formatted for the company's country, as on Bizsure.
        cls.company.country_id = cls.env.ref("base.ke")
        cls.currency = cls.company.currency_id
        Tax = cls.env["account.tax"]
        cls.levy = Tax.create({
            "name": "Test levy 0.2%", "amount": 0.2, "amount_type": "percent",
            "type_tax_use": "sale", "company_id": cls.company.id,
        })
        cls.phcf = Tax.create({
            "name": "Test PHCF 0.25%", "amount": 0.25, "amount_type": "percent",
            "type_tax_use": "sale", "company_id": cls.company.id,
        })
        cls.levies = cls.levy | cls.phcf
        cls.stamp = cls._product("Stamp Duty", 40, code="FEE-STAMP", taxes=False)

        # -- Afya: by family size
        sizes = ["Member Only"] + ["Member + %s" % n for n in range(1, 6)]
        below = [7783, 10654, 12376, 14099, 15822, 17544]
        above = [9793, 13668, 15219, 16769, 18320, 19870]
        op = [12085, 20121, 20121, 22130, 22130, 22130]
        cls.afya_ip = [cls._product(
            "Afya Inpatient – Plan 1 (KES 200,000) – %s – Below 70" % size, price)
            for size, price in zip(sizes, below)]
        cls.afya70_ip = [cls._product(
            "Afya Inpatient – Plan 1 (KES 200,000) – %s – Above 70" % size, price)
            for size, price in zip(sizes, above)]
        cls.afya_op = [cls._product(
            "Afya Outpatient – KES 50,000 per Family – %s – Below 70" % size, price)
            for size, price in zip(sizes, op)]
        cls.afya_dental = [
            cls._product("Afya Dental – KES 5,000 per Person – Below 70", 1210),
            cls._product("Afya Dental – KES 7,500 per Person – Below 70", 1803),
        ]

        cls.insurer_afya = cls.env["res.partner"].create(
            {"name": "Test Afya Insurer", "is_company": True})
        cls.insurer_jamii = cls.env["res.partner"].create(
            {"name": "Test Jamii Insurer", "is_company": True})

        cls.afya = cls._template("Afya – Plan 1 (KES 200,000) – Below 70", cls.insurer_afya, [
            ("section", "Inpatient – Plan 1: KES 200,000 per family", "Inpatient", "inpatient", 200000, False),
            (cls.afya_ip[0], 1),
            ("note", "Inpatient benefits\n• Overall limit: KES 200,000"),
            ("section", "Optional – outpatient cover: KES 50,000 per family", "Outpatient", "outpatient", 50000, True),
            *[(product, 0) for product in cls.afya_op],
            ("section", "Optional – dental cover (per person, with outpatient cover)", "Dental", "dental", 0, True),
            *[(product, 0) for product in cls.afya_dental],
            ("section", "Statutory charges", "Statutory charges", "statutory_charges", 0, False),
            (cls.stamp, 1),
            ("section", "Payment options", "Payment options", "payment_options", 0, False),
            ("note", "Pay in full, or a 25% deposit and the rest monthly."),
        ])
        cls.afya70 = cls._template("Afya – Plan 1 (KES 200,000) – Above 70", cls.insurer_afya, [
            ("section", "Inpatient – Plan 1: KES 200,000 per family", "Inpatient", "inpatient", 200000, False),
            (cls.afya70_ip[0], 1),
            ("section", "Statutory charges", "Statutory charges", "statutory_charges", 0, False),
            (cls.stamp, 1),
        ])

        # -- Jamii: per person, by role and age band
        def jamii(role, band, price):
            return cls._product(
                "Jamii Inpatient (Family) – KES 500,000 – %s (%s Years)" % (role, band), price)
        cls.jamii_p21 = jamii("Principal", "21–40", 21286)
        cls.jamii_p41 = jamii("Principal", "41–54", 27775)
        cls.jamii_s21 = jamii("Spouse", "21–40", 17289)
        cls.jamii_c = jamii("Child", "Day 1 to 20", 9033)
        cls.jamii_op21 = cls._product("Jamii Outpatient – KES 100,000 per Person (21–40 Years)", 34079)
        cls.jamii_op00 = cls._product("Jamii Outpatient – KES 100,000 per Person (Day 1 to 20 Years)", 28398)
        cls.jamii_op41 = cls._product("Jamii Outpatient – KES 100,000 per Person (41–54 Years)", 40894)
        cls.hosi_m = cls._product("Hosi Cash Rider – Option 1 (KES 30,000) – Main Member", 600)
        cls.hosi_d = cls._product("Hosi Cash Rider – Option 1 (KES 30,000) – Each Dependant", 300)
        cls.femina_silver = cls._product("Femina Plus Rider – Silver", 6000)
        cls.femina_gold = cls._product("Femina Plus Rider – Gold", 12000)
        cls.jamii = cls._template("Jamii – Family Cover (KES 500,000)", cls.insurer_jamii, [
            ("section", "Inpatient – KES 500,000 annual limit shared by the family", "Inpatient", "inpatient", 500000, False),
            (cls.jamii_p21, 1), (cls.jamii_s21, 1), (cls.jamii_c, 1),
            ("section", "Optional – outpatient cover (per person)", "Outpatient", "outpatient", 0, True),
            (cls.jamii_op21, 0), (cls.jamii_op00, 0),
            ("section", "Optional – riders", "Riders", "riders", 0, True),
            ("subsection", "Femina Plus"),
            (cls.femina_silver, 0), (cls.femina_gold, 0),
            ("subsection", "Hosi Cash"),
            (cls.hosi_m, 0), (cls.hosi_d, 0),
            ("section", "Statutory charges", "Statutory charges", "statutory_charges", 0, False),
            (cls.stamp, 1),
        ])
        cls.jamii_child = cls._product("Jamii Inpatient (Child Only) – KES 1,000,000", 21281)
        cls.jamii_children = cls._template("Jamii – Child-Only Cover", cls.insurer_jamii, [
            ("section", "Inpatient – child-only cover", "Inpatient", "inpatient", 0, False),
            (cls.jamii_child, 1),
            ("section", "Statutory charges", "Statutory charges", "statutory_charges", 0, False),
            (cls.stamp, 1),
        ])
        # Templates of other companies' catalogues, or not medical, stay out.
        cls.env["sale.order.template"].search([
            ("id", "not in", (cls.afya | cls.afya70 | cls.jamii | cls.jamii_children).ids),
        ]).write({"odin_medical": False})

        cls.agent = new_test_user(
            cls.env, login="quote.agent", name="Quote Agent",
            groups="sales_team.group_sale_manager,base.group_partner_manager",
        )
        cls.Quote = cls.env["odin.medical.quote"].with_user(cls.agent)

    @classmethod
    def _product(cls, name, price, code=False, taxes=True):
        return cls.env["product.product"].create({
            "name": name,
            "default_code": code,
            "type": "service",
            "list_price": price,
            "invoice_policy": "order",
            "taxes_id": [Command.set(cls.levies.ids if taxes else [])],
        })

    @classmethod
    def _template(cls, name, insurer, rows):
        lines = []
        for sequence, row in enumerate(rows, start=1):
            values = {"sequence": sequence * 10}
            kind = row[0] if isinstance(row[0], str) else "product"
            if kind == "section":
                _kind, title, label, key, limit, optional = row
                values.update(display_type="line_section", name=title, benefit_label=label,
                              benefit_key=key, cover_limit=limit, is_optional=optional)
            elif kind == "subsection":
                values.update(display_type="line_subsection", name=row[1])
            elif kind == "note":
                values.update(display_type="line_note", name=row[1])
            else:
                product, qty = row
                values.update(product_id=product.id, product_uom_qty=qty)
            lines.append(Command.create(values))
        return cls.env["sale.order.template"].create({
            "name": name,
            "odin_insurer_id": insurer.id,
            "sale_order_template_line_ids": lines,
        })

    # -- helpers ------------------------------------------------------------

    def dob(self, age):
        """A date of birth for someone ``age`` today, well clear of a birthday."""
        born = fields.Date.context_today(self.Quote) - relativedelta(years=age, months=3)
        return fields.Date.to_string(born)

    def cover(self, **values):
        """The family of the example: principal 35, spouse 28, three children."""
        return dict({
            "principal_dob": self.dob(35),
            "include_spouse": True,
            "spouse_dob": self.dob(28),
            "children": 3,
        }, **values)

    def search(self, **cover):
        return self.Quote.search_plans({"cover": self.cover(**cover)})

    def plan(self, result, template):
        return next(p for p in result["plans"] if p["id"] == template.id)

    def group(self, plan, benefit_key):
        return next(g for g in plan["groups"] if g["benefit_key"] == benefit_key)

    def option(self, plan, benefit_key, index=0, label=None):
        options = self.group(plan, benefit_key)["options"]
        if label:
            return next(o for o in options if o["label"] == label)["key"]
        return options[index]["key"]
