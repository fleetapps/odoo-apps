from odoo import api, models
from odoo.exceptions import ValidationError


class OdinBar(models.Model):
    """The bars the POS report lists are the bars of Bar Control: a bar with
    a POS group name. Its location, POS sales type (SAL-xx), delivery contact
    and analytic account are what the import posts with."""
    _inherit = 'odin.bar'

    @api.constrains('pos_suffix')
    def _check_pos_suffix(self):
        for bar in self:
            if bar.pos_suffix and (not bar.pos_suffix.isalnum() or bar.pos_suffix != bar.pos_suffix.upper()):
                raise ValidationError(self.env._(
                    "The POS suffix of %(bar)s must be letters or digits in capitals, without "
                    "brackets (e.g. BB).", bar=bar.name))

    def _pos_import_issues(self):
        """What stops the import from posting these bars' sales, one sentence per bar."""
        issues = []
        for bar in self:
            missing = []
            if not bar.pos_suffix:
                missing.append(self.env._("POS suffix"))
            if not bar.sale_type_id:
                missing.append(self.env._("POS sales type"))
            if not bar.partner_id:
                missing.append(self.env._("POS delivery contact"))
            if not bar.analytic_account_id:
                missing.append(self.env._("analytic account"))
            if missing:
                issues.append(self.env._(
                    "Set the %(fields)s of %(bar)s under Bar Control > Configuration > Bars.",
                    fields=", ".join(missing), bar=bar.name))
        return issues

    def _pos_import_action(self, day):
        """The import of ``day`` under review, or a new import screen."""
        action = super()._pos_import_action(day)
        if action or not self.env.user.has_group('gymkhana_pos_import.group_pos_import_user'):
            return action
        pending = self.env['pos.import'].search([
            ('company_id', '=', self.company_id.id), ('business_date', '=', day), ('state', '=', 'review')], limit=1)
        if pending:
            return {'type': 'ir.actions.act_window', 'res_model': 'pos.import', 'res_id': pending.id,
                    'views': [(False, 'form')], 'target': 'current'}
        return self.env['ir.actions.act_window']._for_xml_id('gymkhana_pos_import.action_pos_import_new')
