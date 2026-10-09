# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import models
from odoo.exceptions import UserError


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    def _create_payments(self):
        """Hold payment until the credit's document set is complete.

        This is the documentary condition itself, enforced at the only point
        that matters — the moment money is about to move. Posting the bill is
        fine; paying it before the documents are in is what the credit exists
        to prevent.

        There is deliberately no override flag. The two legitimate ways past it
        are to record the documents that arrived, or to detach the credit from
        the bill — both of which leave a trace. A silent bypass would not.
        """
        blocked = self.line_ids.move_id.filtered("odin_lc_payment_blocked")
        if blocked:
            details = "\n".join(
                self.env._(
                    " • %(move)s under %(lc)s — %(count)s document(s) outstanding: %(docs)s",
                    move=move.display_name,
                    lc=move.odin_lc_id.name,
                    count=move.odin_lc_documents_outstanding,
                    docs=", ".join(
                        move.odin_lc_id.document_ids.filtered(
                            lambda d: d.is_required and not d.is_received
                        ).mapped("document_type_id.name")
                    )
                    or self.env._("none listed"),
                )
                for move in blocked
            )
            raise UserError(
                self.env._(
                    "Payment is held by the documentary conditions of a letter "
                    "of credit:\n\n%(details)s\n\n"
                    "Record the documents on the credit, or detach the credit "
                    "from the document, before paying.",
                    details=details,
                )
            )
        return super()._create_payments()
