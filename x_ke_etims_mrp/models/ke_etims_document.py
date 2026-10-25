# -*- coding: utf-8 -*-
from odoo import fields, models

from odoo.addons.x_ke_etims.backends import Receipt


class KeEtimsDocument(models.Model):
    _inherit = "ke.etims.document"

    kind = fields.Selection(selection_add=[("item_composition", "Item Composition")],
                            ondelete={"item_composition": "cascade"})

    def _ke_etims_dispatch(self, adapter, data):
        if self.kind == "item_composition":
            # One request per component, as KRA's endpoint takes them.
            for composition in data:
                adapter.save_item_composition(composition)
            return Receipt(status="signed")
        return super()._ke_etims_dispatch(adapter, data)
