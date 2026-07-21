# -*- coding: utf-8 -*-
from odoo import fields, models


class FleetVehicleTracker(models.Model):
    _name = "fleet.vehicle.tracker"
    _description = "Vehicle GPS Tracker"

    name = fields.Char(required=True)
    provider_id = fields.Many2one("fleet.tracking.provider", required=True,
                                  ondelete="cascade")
    external_id = fields.Char(required=True, index=True)
    vehicle_id = fields.Many2one("fleet.vehicle", index=True)
    last_latitude = fields.Float(digits=(10, 7))
    last_longitude = fields.Float(digits=(10, 7))
    last_speed_kph = fields.Float()
    last_seen = fields.Datetime()
    speed_limit_kph = fields.Float(default=0.0,
                                   help="0 = no speeding alerts for this vehicle.")

    _sql_constraints = [("uniq_dev", "unique(provider_id, external_id)",
                         "Device already mapped.")]

    def _sync_odometer(self, odometer_km):
        """Write into core Fleet's odometer log (fleet.vehicle.odometer) so
        Odoo's native services/contract reminders use live GPS mileage."""
        self.ensure_one()
        if not self.vehicle_id:
            return
        last = self.vehicle_id.odometer
        if odometer_km > last + 1:  # only forward movement, 1km hysteresis
            self.env["fleet.vehicle.odometer"].create({
                "vehicle_id": self.vehicle_id.id, "value": odometer_km})


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    tracker_ids = fields.One2many("fleet.vehicle.tracker", "vehicle_id")
    last_position = fields.Char(compute="_compute_last_position")

    def _compute_last_position(self):
        for v in self:
            t = v.tracker_ids[:1]
            v.last_position = (f"{t.last_latitude:.5f}, {t.last_longitude:.5f}"
                               if t and t.last_latitude else "")
