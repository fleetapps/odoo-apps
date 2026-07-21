# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta
from odoo import api, fields, models


class FleetVehiclePosition(models.Model):
    _name = "fleet.vehicle.position"
    _description = "GPS Position"
    _order = "position_time desc"

    tracker_id = fields.Many2one("fleet.vehicle.tracker", required=True,
                                 ondelete="cascade", index=True)
    vehicle_id = fields.Many2one("fleet.vehicle", index=True)
    latitude = fields.Float(digits=(10, 7), required=True)
    longitude = fields.Float(digits=(10, 7), required=True)
    speed_kph = fields.Float()
    ignition = fields.Boolean()
    position_time = fields.Datetime(index=True)

    @api.model
    def cron_purge(self):
        """Positions are high volume: default retention 3 months."""
        months = int(self.env["ir.config_parameter"].sudo().get_param(
            "fleet_tracking_connector.retention_months", "3"))
        cutoff = fields.Datetime.now() - relativedelta(months=months)
        self.search([("position_time", "<", cutoff)]).unlink()
