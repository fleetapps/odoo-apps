# -*- coding: utf-8 -*-
"""Provider account + adapter contract.

Design: one `fleet.tracking.provider` record per platform account; a python
adapter class per platform implementing a 4-method contract:
  auth()                -> session/token
  list_devices()        -> [{external_id, name, plate}]
  fetch_positions(ids)  -> [{external_id, lat, lon, speed_kph, ts, odometer_km, ignition}]
  fetch_trips(id, dt)   -> [{start, end, km, max_speed}]   (optional)

Platform API references (VERIFY-ON-BUILD - endpoints & auth evolve):
* Wialon Remote API:    https://sdk.wialon.com/wiki/en/sidebar/remoteapi/apiref/apiref (token login)
* Geotab (MyGeotab):    https://developers.geotab.com/myGeotab/introduction  (JSON-RPC, mygeotab-python SDK)
* Samsara:              https://developers.samsara.com/reference             (REST, Bearer token)
* TrackSolid (Jimi IoT):https://www.jimicloud.com / TrackSolid Pro open API  (appkey+sign; confirm current portal)
* Teltonika:            devices speak Codec8 to a TCP server, NOT a cloud REST API.
                        Supported here via an aggregator (e.g. flespi.io REST) or
                        Teltonika FOTA-linked platform. Direct TCP ingest is a
                        Phase-2 microservice, out of module scope.
"""
import logging
from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class FleetTrackingProvider(models.Model):
    _name = "fleet.tracking.provider"
    _description = "GPS Tracking Provider Account"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    platform = fields.Selection(
        [("wialon", "Wialon"), ("teltonika", "Teltonika (via aggregator)"),
         ("tracksolid", "TrackSolid"), ("geotab", "Geotab"),
         ("samsara", "Samsara")], required=True)
    base_url = fields.Char(help="Override for regional endpoints / aggregator URL.")
    api_token = fields.Char(groups="fleet.fleet_group_manager")
    api_secret = fields.Char(groups="fleet.fleet_group_manager")
    account = fields.Char(help="Geotab database / Wialon account where applicable.")
    poll_minutes = fields.Integer(default=5)
    last_poll = fields.Datetime()

    def _adapter(self):
        from . import providers_impl
        cls = providers_impl.ADAPTERS.get(self.platform)
        if not cls:
            raise UserError(f"No adapter for {self.platform}")
        return cls(self)

    def action_test_connection(self):
        self.ensure_one()
        self._adapter().auth()
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": "Connected", "type": "success"}}

    def action_discover_devices(self):
        """Pull the platform's device list and offer mapping to fleet.vehicle."""
        self.ensure_one()
        Tracker = self.env["fleet.vehicle.tracker"]
        for dev in self._adapter().list_devices():
            if not Tracker.search_count([("provider_id", "=", self.id),
                                         ("external_id", "=", dev["external_id"])]):
                vehicle = dev.get("plate") and self.env["fleet.vehicle"].search(
                    [("license_plate", "=ilike", dev["plate"])], limit=1)
                Tracker.create({
                    "provider_id": self.id,
                    "external_id": dev["external_id"],
                    "name": dev.get("name") or dev["external_id"],
                    "vehicle_id": vehicle and vehicle.id or False})

    @api.model
    def cron_poll(self):
        for provider in self.search([("active", "=", True)]):
            try:
                provider._poll()
                provider.last_poll = fields.Datetime.now()
            except Exception as e:  # noqa: BLE001 - one bad provider must not stop others
                _logger.exception("Polling %s failed: %s", provider.name, e)

    def _poll(self):
        self.ensure_one()
        trackers = self.env["fleet.vehicle.tracker"].search(
            [("provider_id", "=", self.id), ("vehicle_id", "!=", False)])
        if not trackers:
            return
        adapter = self._adapter()
        positions = adapter.fetch_positions(trackers.mapped("external_id"))
        by_ext = {t.external_id: t for t in trackers}
        Position = self.env["fleet.vehicle.position"]
        for p in positions:
            tracker = by_ext.get(str(p["external_id"]))
            if not tracker:
                continue
            Position.create({
                "tracker_id": tracker.id, "vehicle_id": tracker.vehicle_id.id,
                "latitude": p["lat"], "longitude": p["lon"],
                "speed_kph": p.get("speed_kph", 0.0),
                "ignition": p.get("ignition", False),
                "position_time": p.get("ts") or fields.Datetime.now()})
            tracker.write({"last_latitude": p["lat"], "last_longitude": p["lon"],
                           "last_speed_kph": p.get("speed_kph", 0.0),
                           "last_seen": p.get("ts") or fields.Datetime.now()})
            # odometer sync into core Fleet (fleet.vehicle.odometer)
            if p.get("odometer_km"):
                tracker._sync_odometer(p["odometer_km"])
        self.env["fleet.tracking.alert.engine"].evaluate(trackers)
