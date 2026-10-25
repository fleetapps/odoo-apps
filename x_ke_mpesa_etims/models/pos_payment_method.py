# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import models


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    def _ke_etims_guess_payment_type(self):
        """An M-Pesa terminal is mobile money whatever the method is called:
        eTIMS would otherwise take any payment terminal for a card."""
        if self.use_payment_terminal == "mpesa":
            return "06"
        return super()._ke_etims_guess_payment_type()
