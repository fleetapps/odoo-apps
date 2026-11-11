import re

from odoo import fields, models

# "500 Ml", "5 Litre", "20 Litres", "750 ml", "1 kg", "400 g" in a product name
_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|millilit(?:re|er)s?|l|ltrs?|lit(?:re|er)s?|kgs?|kilograms?|g|gm|grams?)\b",
                   re.IGNORECASE)


def _number(value):
    return f"{value:.3f}".rstrip("0").rstrip(".")


def pack_size_from_name(name):
    """0.5 L for "Handwash 500 Ml", 20 L for "Bleach 20 Litre", 65 ml for a
    sachet: the size the export documents print, read from the name."""
    matches = _SIZE.findall(name or "")
    if not matches:
        return ""
    value, unit = matches[-1]
    value, unit = float(value.replace(",", ".")), unit.lower()
    if unit.startswith("m"):
        return f"{_number(value / 1000)} L" if value >= 100 else f"{_number(value)} ml"
    if unit.startswith("k"):
        return f"{_number(value)} kg"
    if unit.startswith("g"):
        return f"{_number(value / 1000)} kg" if value >= 1000 else f"{_number(value)} g"
    return f"{_number(value)} L"


class ProductTemplate(models.Model):
    """What the export documents print per product, beside the HS code and
    country of origin (Inventory tab) and the weight Odoo already has."""

    _inherit = "product.template"

    doc_kebs_permit = fields.Char(string="KEBS permit", help="e.g. SM#56791, printed on the commercial invoice.")
    doc_brand = fields.Char(string="Brand", help="Empty: the company's brand on export documents.")
    doc_pack_size = fields.Char(string="Pack size",
                                help="As the export documents print it, e.g. 0.5 L. Empty: read from the name.")
    doc_net_weight = fields.Float(string="Net weight (kg)", digits="Stock Weight",
                                  help="Contents only. Weight (Inventory tab) is the gross weight of one unit.")
    doc_carton_qty = fields.Integer(string="Units per carton", help="Cartons on packing lists: quantity / units per carton.")

    def _doc_pack_size(self):
        self.ensure_one()
        return self.doc_pack_size or pack_size_from_name(self.name)


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _doc_pack_size(self):
        self.ensure_one()
        return self.product_tmpl_id._doc_pack_size()
