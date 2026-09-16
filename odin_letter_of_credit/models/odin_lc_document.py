# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class OdinLcDocument(models.Model):
    """One line of the credit's document set, and whether it has arrived.

    This is the list payment is released against — see
    account.payment.register in this module.
    """

    _name = "odin.lc.document"
    _description = "Letter of Credit Document"
    _order = "sequence, id"
    _rec_name = "display_name"

    lc_id = fields.Many2one(
        "odin.letter.of.credit", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="lc_id.company_id", store=True)
    document_type_id = fields.Many2one(
        "odin.lc.document.type", required=True, ondelete="restrict"
    )
    sequence = fields.Integer(default=10)
    display_name = fields.Char(compute="_compute_display_name")
    is_required = fields.Boolean(
        string="Required",
        default=True,
        help="Uncheck for a document the credit mentions but does not require "
        "for a compliant presentation.",
    )
    copies_required = fields.Integer(default=1)
    is_received = fields.Boolean(string="Received", tracking=True)
    date_received = fields.Date(readonly=True, copy=False)
    reference = fields.Char(help="Document number, B/L number, certificate number.")
    issuer = fields.Char()
    note = fields.Text()

    _copies_positive = models.Constraint(
        "CHECK(copies_required >= 0)", "Copies required cannot be negative."
    )
    _type_per_lc_uniq = models.Constraint(
        "UNIQUE(lc_id, document_type_id)",
        "That document is already listed on this credit.",
    )

    @api.depends("document_type_id.name", "reference")
    def _compute_display_name(self):
        for doc in self:
            label = doc.document_type_id.name or ""
            doc.display_name = f"{label} ({doc.reference})" if doc.reference else label

    @api.constrains("is_received", "reference")
    def _check_reference_on_receipt(self):
        for doc in self:
            if doc.is_received and doc.is_required and not doc.reference:
                raise ValidationError(
                    self.env._(
                        "Record the document number for %(doc)s. A document set "
                        "ticked off without references cannot be checked against "
                        "the presentation later.",
                        doc=doc.document_type_id.name,
                    )
                )

    def write(self, vals):
        if "is_received" in vals:
            vals["date_received"] = (
                fields.Date.context_today(self) if vals["is_received"] else False
            )
        res = super().write(vals)
        if "is_received" in vals:
            for doc in self:
                doc.lc_id.message_post(
                    body=self.env._(
                        "%(doc)s marked %(status)s.",
                        doc=doc.document_type_id.name,
                        status=self.env._("received")
                        if doc.is_received
                        else self.env._("outstanding"),
                    )
                )
        return res

    def action_toggle_received(self):
        for doc in self:
            doc.is_received = not doc.is_received
        return True
