# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class OdinLcDocumentType(models.Model):
    """The documents a credit can call for.

    Configurable rather than a fixed selection: the document set is dictated by
    the credit, and every trade lane has its own additions — a phytosanitary
    certificate here, a pre-shipment inspection there.
    """

    _name = "odin.lc.document.type"
    _description = "Letter of Credit Document Type"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(help="Short code used on the document checklist.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    is_transport_document = fields.Boolean(
        help="Transport documents evidence shipment, so the latest shipment date "
        "is checked against them rather than against the expiry date.",
    )
    description = fields.Text()

    _code_uniq = models.Constraint(
        "UNIQUE(code)",
        "A document type with this code already exists.",
    )
