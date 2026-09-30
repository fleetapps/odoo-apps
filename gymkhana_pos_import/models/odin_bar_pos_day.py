from odoo import fields, models
from odoo.exceptions import UserError


class OdinBarPosDay(models.Model):
    _inherit = 'odin.bar.pos.day'

    pos_import_id = fields.Many2one(
        'pos.import', string="POS import", readonly=True, index='btree_not_null', ondelete='cascade')

    def unlink(self):
        if not self.env.context.get('odin_bar_pos_unmark'):
            posted = self.filtered('pos_import_id')[:1]
            if posted:
                raise UserError(self.env._(
                    "%(day)s was posted by %(pos_import)s: undo that import instead.",
                    day=posted.display_name, pos_import=posted.pos_import_id.name))
        return super().unlink()
