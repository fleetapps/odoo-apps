from odoo import fields, models


class OdinAiRun(models.Model):
    _inherit = "odin.ai.run"

    feature = fields.Selection(selection_add=[("stock_photo", "Stock Book photo")],
                               ondelete={"stock_photo": "set null"})
