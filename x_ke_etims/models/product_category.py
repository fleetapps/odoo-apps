# -*- coding: utf-8 -*-
"""A KRA item class per product category, which its products take by default.

A catalogue is classified a category at a time instead of a product at a
time. A category without one takes its parent's, so a class set on "All" is
the fallback for every product -- including the down payment and discount
products Sales creates on its own.
"""

from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    ke_etims_item_class_id = fields.Many2one(
        "ke.etims.item.class", string="KRA Item Class",
        help="The KRA item class of this category's products, unless a product "
             "says otherwise. A category without one takes its parent's.")

    def _ke_etims_item_class(self):
        """This category's class, or the nearest parent's."""
        category = self
        while category:
            if category.ke_etims_item_class_id:
                return category.ke_etims_item_class_id
            category = category.parent_id
        return self.env["ke.etims.item.class"]
