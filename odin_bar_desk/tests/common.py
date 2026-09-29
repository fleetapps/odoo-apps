import uuid

from odoo import Command
from odoo.tests import TransactionCase, new_test_user


class BarDeskCase(TransactionCase):
    """A store and two bars set up like Dhostana's: bars are internal locations
    under the store's warehouse, each with its own ISS/RET/SAL operation types,
    and shared IBT/ROMA/EVT/DEBT types. Spirits are kept in tots with bottle
    sizes as packagings; beer in units with a crate packaging."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.loc_store = cls.warehouse.lot_stock_id
        Location = cls.env["stock.location"]
        view = cls.warehouse.view_location_id
        cls.loc_be = Location.create({"name": "Bulls Eye (BE)", "usage": "internal", "location_id": view.id})
        cls.loc_bb = Location.create({"name": "Banda Bar (BB)", "usage": "internal", "location_id": view.id})
        cls.loc_customers = cls.env.ref("stock.stock_location_customers")
        cls.loc_roma = Location.create({"name": "Roma", "usage": "customer", "location_id": cls.loc_customers.id})
        cls.loc_events = Location.create({"name": "Events", "usage": "customer", "location_id": cls.loc_customers.id})
        cls.loc_unpaid = Location.create(
            {"name": "Unpaid Bills", "usage": "customer", "location_id": cls.loc_customers.id}
        )

        def picking_type(name, code, sequence_code, source, destination):
            return cls.env["stock.picking.type"].create(
                {
                    "name": name,
                    "code": code,
                    "sequence_code": sequence_code,
                    "default_location_src_id": source.id,
                    "default_location_dest_id": destination.id,
                    "warehouse_id": cls.warehouse.id,
                    "company_id": cls.company.id,
                }
            )

        cls.type_iss_be = picking_type("Issue to BE", "internal", "ISS-BE", cls.loc_store, cls.loc_be)
        cls.type_iss_bb = picking_type("Issue to BB", "internal", "ISS-BB", cls.loc_store, cls.loc_bb)
        cls.type_ret_be = picking_type("Return from BE", "internal", "RET-BE", cls.loc_be, cls.loc_store)
        cls.type_ret_bb = picking_type("Return from BB", "internal", "RET-BB", cls.loc_bb, cls.loc_store)
        cls.type_sal_be = picking_type("BE POS Sales", "outgoing", "SAL-BE", cls.loc_be, cls.loc_customers)
        cls.type_sal_bb = picking_type("BB POS Sales", "outgoing", "SAL-BB", cls.loc_bb, cls.loc_customers)
        cls.type_ibt = picking_type("Inter-bar Transfer", "internal", "IBT", cls.loc_be, cls.loc_bb)
        cls.type_roma = picking_type("Roma Out", "outgoing", "ROMA", cls.loc_be, cls.loc_roma)
        cls.type_evt = picking_type("Event Issue", "outgoing", "EVT", cls.loc_be, cls.loc_events)
        cls.type_debt = picking_type("Unpaid Bill", "outgoing", "DEBT", cls.loc_be, cls.loc_unpaid)

        Uom = cls.env["uom.uom"]
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")
        cls.uom_tot = Uom.create({"name": "Tot"})
        cls.uom_750 = Uom.create(
            {"name": "Bottle 750ml (25 tots)", "relative_factor": 25, "relative_uom_id": cls.uom_tot.id}
        )
        cls.uom_1l = Uom.create(
            {"name": "Bottle 1L (33 tots)", "relative_factor": 33, "relative_uom_id": cls.uom_tot.id}
        )
        cls.uom_crate = Uom.create(
            {"name": "Crate of 24", "relative_factor": 24, "relative_uom_id": cls.uom_unit.id}
        )

        Product = cls.env["product.product"]
        cls.jameson = Product.create(
            {
                "name": "Jameson",
                "is_storable": True,
                "uom_id": cls.uom_tot.id,
                "uom_ids": [Command.set([cls.uom_750.id, cls.uom_1l.id])],
                "bar_desk_ok": True,
                "standard_price": 80.0,
            }
        )
        cls.gordons = Product.create(
            {
                "name": "Gordon's",
                "is_storable": True,
                "uom_id": cls.uom_tot.id,
                "uom_ids": [Command.set([cls.uom_750.id])],
                "bar_desk_ok": True,
                "standard_price": 60.0,
            }
        )
        cls.tusker = Product.create(
            {
                "name": "Tusker Lager 500ml",
                "is_storable": True,
                "uom_id": cls.uom_unit.id,
                "uom_ids": [Command.set([cls.uom_crate.id])],
                "bar_desk_ok": True,
                "standard_price": 150.0,
            }
        )
        cls.not_on_desk = Product.create({"name": "Office paper", "is_storable": True})

        Bar = cls.env["odin.bar"]
        cls.store = Bar.create(
            {
                "name": "Main Store",
                "code": "MS",
                "kind": "store",
                "location_id": cls.loc_store.id,
                "pos_required": False,
                "tz": "Africa/Nairobi",
            }
        )
        cls.bar_be = Bar.create(
            {"name": "Bulls Eye", "code": "BE", "location_id": cls.loc_be.id, "tz": "Africa/Nairobi"}
        )
        cls.bar_bb = Bar.create(
            {"name": "Banda Bar", "code": "BB", "location_id": cls.loc_bb.id, "tz": "Africa/Nairobi"}
        )
        (cls.store | cls.bar_be | cls.bar_bb).action_autofill_setup()
        cls.reasons = {
            reason.code: reason
            for reason in cls.env["odin.bar.reason"].search([("company_id", "=", cls.company.id)])
        }

        Employee = cls.env["hr.employee"]
        cls.mary = Employee.create(
            {"name": "Mary", "pin": "1234", "odin_bar_ids": [Command.set(cls.bar_be.ids)]}
        )
        cls.john = Employee.create(
            {"name": "John", "pin": "5678", "odin_bar_ids": [Command.set(cls.bar_bb.ids)]}
        )
        cls.sam = Employee.create(
            {"name": "Sam", "pin": "4321", "odin_bar_ids": [Command.set(cls.store.ids)]}
        )

        cls.device_be = new_test_user(
            cls.env, login="be.desk", groups="odin_bar_desk.group_bar_desk_staff", odin_bar_id=cls.bar_be.id
        )
        cls.device_bb = new_test_user(
            cls.env, login="bb.desk", groups="odin_bar_desk.group_bar_desk_staff", odin_bar_id=cls.bar_bb.id
        )
        cls.device_store = new_test_user(
            cls.env, login="ms.desk", groups="odin_bar_desk.group_bar_desk_staff", odin_bar_id=cls.store.id
        )
        cls.manager = new_test_user(
            cls.env, login="bar.manager", groups="odin_bar_desk.group_bar_desk_manager"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def desk(self, user):
        return self.env["odin.bar.desk"].with_user(user)

    def login(self, user, bar, employee, pin):
        result = self.desk(user).desk_login(bar.id, employee.id, pin)
        self.assertIn("token", result, result)
        return result["token"]

    @staticmethod
    def uuid():
        return str(uuid.uuid4())

    def qty(self, product, location):
        """On-hand quantity in the product's unit."""
        return product.with_context(location=location.id).qty_available

    def post(self, picking_type, source, destination, lines, business_date=None):
        """Create and validate a transfer as superuser, like the backend would."""
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": picking_type.id,
                "location_id": source.id,
                "location_dest_id": destination.id,
                "bar_business_date": business_date,
                "move_ids": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "product_uom": (uom or product.uom_id).id,
                            "product_uom_qty": qty,
                            "location_id": source.id,
                            "location_dest_id": destination.id,
                        }
                    )
                    for product, qty, uom in lines
                ],
            }
        )
        self.env["odin.bar.desk"]._desk_validate(picking)
        return picking

    @classmethod
    def opening_stock(cls, location, lines):
        """Put stock somewhere through a real inventory move, so it has history."""
        for product, qty in lines:
            quant = cls.env["stock.quant"].with_context(inventory_mode=True).create(
                {"product_id": product.id, "location_id": location.id, "inventory_quantity": qty}
            )
            quant.action_apply_inventory()

    def pos_sales(self, bar, business_date, lines, mark_posted=True):
        """What the POS importer does: a sales delivery out of the bar for a
        trading day, then the day marked as posted."""
        picking = self.post(
            bar.sale_type_id,
            bar.location_id,
            self.loc_customers,
            [(product, qty, None) for product, qty in lines],
            business_date=business_date,
        )
        if mark_posted:
            bar._mark_pos_posted(business_date, source="test import")
        return picking

    def count_line(self, product, *, units=0.0, bottles=None, open_tots=0.0):
        return {
            "product_id": product.id,
            "touched": True,
            "unit_qty": units,
            "bottle_detail": {str(uom.id): qty for uom, qty in (bottles or {}).items()},
            "open_tots": open_tots,
        }

    def submit_count(self, user, bar, employee, pin, lines, kind="closing"):
        """Start, fill and submit a count from the Desk. Returns the count."""
        token = self.login(user, bar, employee, pin)
        desk = self.desk(user)
        data = desk.desk_count_start(bar.id, token, kind=kind)
        counted = {line["product_id"] for line in lines}
        if kind == "closing":
            # Everything else on the sheet is counted as zero.
            lines = lines + [
                self.count_line(self.env["product.product"].browse(line["product_id"]))
                for line in data["lines"]
                if line["product_id"] not in counted
            ]
        desk.desk_count_submit(bar.id, token, self.uuid(), data["id"], lines)
        return self.env["odin.bar.count"].browse(data["id"])
