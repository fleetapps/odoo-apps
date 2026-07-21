# -*- coding: utf-8 -*-
"""Alert engine: speeding + geofence (circle) evaluated on every poll.
Notifications through mail activities on the vehicle (native Fleet UX)."""
import math
from odoo import api, fields, models


class GeoFence(models.Model):
    _name = "fleet.tracking.geofence"
    _description = "Geofence (circular)"

    name = fields.Char(required=True)
    center_lat = fields.Float(digits=(10, 7), required=True)
    center_lon = fields.Float(digits=(10, 7), required=True)
    radius_m = fields.Integer(required=True, default=500)
    mode = fields.Selection([("enter", "Alert on Enter"),
                             ("exit", "Alert on Exit")], default="exit")
    vehicle_ids = fields.Many2many("fleet.vehicle")


class AlertEngine(models.AbstractModel):
    _name = "fleet.tracking.alert.engine"
    _description = "Tracking Alert Engine"

    @api.model
    def _haversine_m(self, lat1, lon1, lat2, lon2):
        r = 6371000
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = (math.sin(dp / 2) ** 2 +
             math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
        return 2 * r * math.asin(math.sqrt(a))

    @api.model
    def evaluate(self, trackers):
        for t in trackers.filtered(lambda t: t.last_latitude):
            # speeding
            if t.speed_limit_kph and t.last_speed_kph > t.speed_limit_kph:
                self._notify(t.vehicle_id,
                             f"Speeding: {t.last_speed_kph:.0f} km/h "
                             f"(limit {t.speed_limit_kph:.0f})")
            # geofences
            for gf in self.env["fleet.tracking.geofence"].search(
                    [("vehicle_ids", "in", t.vehicle_id.id)]):
                dist = self._haversine_m(t.last_latitude, t.last_longitude,
                                         gf.center_lat, gf.center_lon)
                inside = dist <= gf.radius_m
                if (gf.mode == "exit" and not inside) or \
                        (gf.mode == "enter" and inside):
                    self._notify(t.vehicle_id,
                                 f"Geofence {gf.name}: {gf.mode} detected")
                # TODO(build): edge-trigger with last-state memory to avoid
                # repeating the alert every poll while condition persists.

    @api.model
    def _notify(self, vehicle, body):
        if vehicle:
            vehicle.message_post(body=body)
