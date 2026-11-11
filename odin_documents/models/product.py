from odoo import fields, models


class ProductTemplate(models.Model):
    """What the export documents print per product, beside the HS code and
    country of origin (Inventory tab) and the weight Odoo already has."""

    _inherit = "product.template"

    doc_kebs_permit = fields.Char(string="KEBS permit", help="e.g. SM#56791, printed on the commercial invoice.")
    doc_brand = fields.Char(string="Brand", help="Empty: the company's brand on export documents.")
    doc_pack_size = fields.Char(string="Pack size", help="As the export documents print it, e.g. 0.5 L.")
    doc_net_weight = fields.Float(string="Net weight (kg)", digits="Stock Weight",
                                  help="Contents only. Weight (Inventory tab) is the gross weight of one unit.")
    doc_carton_qty = fields.Integer(string="Units per carton", help="Cartons on packing lists: quantity / units per carton.")
