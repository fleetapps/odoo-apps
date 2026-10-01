import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class IrActionsServer(models.Model):
    """"Send WhatsApp" server actions and automation rules (SPEC.md §13.1, R11).

    The state key is the one Odoo Enterprise uses (D1); like ``sms``, actions
    of this type are deleted with the module.
    """

    _inherit = "ir.actions.server"

    state = fields.Selection(
        selection_add=[("whatsapp", "Send WhatsApp"), ("followers",)],
        ondelete={"whatsapp": "cascade"},
    )
    wa_template_id = fields.Many2one(
        "whatsapp_connector.template", "WhatsApp Template",
        compute="_compute_wa_template_id", readonly=False, store=True, ondelete="set null",
        domain="[('model_id', '=', model_id), ('status', '=', 'APPROVED')]",
    )

    def _name_depends(self):
        return [*super()._name_depends(), "wa_template_id"]

    def _generate_action_name(self):
        self.ensure_one()
        if self.state == "whatsapp" and self.wa_template_id:
            return self.env._("Send WhatsApp %(template)s", template=self.wa_template_id.name)
        return super()._generate_action_name()

    @api.depends("state")
    def _compute_available_model_ids(self):
        whatsapp = self.filtered(lambda action: action.state == "whatsapp")
        if whatsapp:
            models_ = self.env["ir.model"].search([("is_mail_thread", "=", True), ("transient", "=", False)])
            for action in whatsapp:
                action.available_model_ids = models_.ids
        super(IrActionsServer, self - whatsapp)._compute_available_model_ids()

    @api.depends("model_id", "state")
    def _compute_wa_template_id(self):
        self.filtered(
            lambda action: action.state != "whatsapp" or action.model_id != action.wa_template_id.model_id,
        ).wa_template_id = False

    @api.model
    def _warning_depends(self):
        return [*super()._warning_depends(), "model_id", "state", "wa_template_id"]

    def _get_warning_messages(self):
        self.ensure_one()
        warnings = super()._get_warning_messages()
        if self.state == "whatsapp" and self.wa_template_id and self.wa_template_id.model_id != self.model_id:
            warnings.append(self.env._("The WhatsApp template applies to another model."))
        return warnings

    def _run_action_whatsapp_multi(self, eval_context=None):
        if not self.wa_template_id or self._is_recompute():
            return False
        records = eval_context.get("records") or eval_context.get("record")
        for record in records or []:
            try:
                with self.env.cr.savepoint():
                    self.wa_template_id._wa_send_to_record(record)
            except UserError as e:
                # one unreachable contact must not stop the action for the others
                _logger.info("Send WhatsApp action %s skipped %s: %s", self.id, record, e)
        return False
