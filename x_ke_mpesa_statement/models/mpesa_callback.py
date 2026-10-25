# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import models

from odoo.addons.x_ke_mpesa.lib import daraja


class MpesaCallback(models.Model):
    _inherit = "mpesa.callback"

    def _handle_pull(self, data):
        """Whatever transaction rows Safaricom posts to the Pull callback URL."""
        Tx = self.env["mpesa.transaction"].sudo()
        result = Tx
        for row in daraja.pull_rows(data):
            result |= Tx._from_pull(self.shortcode_id, row)
        return result[:1]
