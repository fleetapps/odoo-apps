# -*- coding: utf-8 -*-
"""Concrete platform adapters. Plain python classes (not models) so adding a
platform is one class + one selection value. All VERIFY-ON-BUILD: confirm each
endpoint against the vendor's current docs before release."""
import logging
import requests

_logger = logging.getLogger(__name__)
TIMEOUT = 30


class BaseAdapter:
    def __init__(self, provider):
        self.p = provider

    def auth(self):  # pragma: no cover - contract
        raise NotImplementedError

    def list_devices(self):
        raise NotImplementedError

    def fetch_positions(self, external_ids):
        raise NotImplementedError


class WialonAdapter(BaseAdapter):
    """Wialon Remote API: token login -> session id (sid), then avl_evts /
    core/search_items for units with position data."""
    BASE = "https://hst-api.wialon.com/wialon/ajax.html"

    def _call(self, svc, params, sid=None):
        payload = {"svc": svc, "params": __import__("json").dumps(params)}
        if sid:
            payload["sid"] = sid
        r = requests.post(self.p.base_url or self.BASE, data=payload, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("error"):
            raise ValueError(f"Wialon error {data['error']}")
        return data

    def auth(self):
        res = self._call("token/login", {"token": self.p.api_token})
        return res["eid"]

    def list_devices(self):
        sid = self.auth()
        res = self._call("core/search_items", {
            "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                     "propValueMask": "*", "sortType": "sys_name"},
            "force": 1, "flags": 1, "from": 0, "to": 0}, sid)
        return [{"external_id": str(i["id"]), "name": i.get("nm")}
                for i in res.get("items", [])]

    def fetch_positions(self, external_ids):
        sid = self.auth()
        res = self._call("core/search_items", {
            "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                     "propValueMask": "*", "sortType": "sys_name"},
            "force": 1, "flags": 1025, "from": 0, "to": 0}, sid)  # 1024 = position flag
        out = []
        for i in res.get("items", []):
            pos = i.get("pos")
            if pos and str(i["id"]) in set(external_ids):
                out.append({"external_id": str(i["id"]), "lat": pos["y"],
                            "lon": pos["x"], "speed_kph": pos.get("s", 0)})
        return out


class GeotabAdapter(BaseAdapter):
    """MyGeotab JSON-RPC: Authenticate -> credentials; Get DeviceStatusInfo."""
    BASE = "https://my.geotab.com/apiv1"

    def _rpc(self, method, params):
        r = requests.post(self.p.base_url or self.BASE,
                          json={"method": method, "params": params}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise ValueError(str(data["error"])[:200])
        return data["result"]

    def auth(self):
        return self._rpc("Authenticate", {
            "userName": self.p.account, "password": self.p.api_secret,
            "database": self.p.api_token})  # token field reused as database name

    def list_devices(self):
        creds = self.auth()["credentials"]
        devices = self._rpc("Get", {"typeName": "Device", "credentials": creds})
        return [{"external_id": d["id"], "name": d.get("name"),
                 "plate": d.get("licensePlate")} for d in devices]

    def fetch_positions(self, external_ids):
        creds = self.auth()["credentials"]
        infos = self._rpc("Get", {"typeName": "DeviceStatusInfo",
                                  "credentials": creds})
        return [{"external_id": i["device"]["id"], "lat": i.get("latitude"),
                 "lon": i.get("longitude"), "speed_kph": i.get("speed", 0)}
                for i in infos if i.get("device", {}).get("id") in set(external_ids)]


class SamsaraAdapter(BaseAdapter):
    """Samsara REST: Bearer token; GET /fleet/vehicles/stats?types=gps."""
    BASE = "https://api.samsara.com"

    def _get(self, path, params=None):
        r = requests.get((self.p.base_url or self.BASE) + path, params=params,
                         headers={"Authorization": f"Bearer {self.p.api_token}"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def auth(self):
        return self._get("/fleet/vehicles", {"limit": 1})

    def list_devices(self):
        data = self._get("/fleet/vehicles", {"limit": 512})
        return [{"external_id": v["id"], "name": v.get("name"),
                 "plate": v.get("licensePlate")} for v in data.get("data", [])]

    def fetch_positions(self, external_ids):
        data = self._get("/fleet/vehicles/stats", {"types": "gps"})
        out = []
        for v in data.get("data", []):
            gps = v.get("gps") or {}
            if str(v.get("id")) in set(map(str, external_ids)) and gps:
                out.append({"external_id": str(v["id"]),
                            "lat": gps.get("latitude"),
                            "lon": gps.get("longitude"),
                            "speed_kph": (gps.get("speedMilesPerHour") or 0) * 1.60934,
                            "odometer_km": None})
        return out


class TrackSolidAdapter(BaseAdapter):
    """TrackSolid / Jimi IoT open API: appkey + md5 signature scheme.
    VERIFY-ON-BUILD against the current Jimi Cloud API portal."""
    BASE = "https://open.tracksolidpro.com/route/rest"

    def auth(self):
        raise NotImplementedError("Wire Jimi appkey+sign auth at build time")

    def list_devices(self):
        return []

    def fetch_positions(self, external_ids):
        return []


class TeltonikaAdapter(BaseAdapter):
    """Teltonika trackers stream Codec8 over TCP - no vendor cloud REST.
    Supported via an aggregator account (flespi.io REST shown as default).
    Direct TCP ingest = Phase-2 standalone gateway service."""
    BASE = "https://flespi.io/gw"

    def _get(self, path):
        r = requests.get((self.p.base_url or self.BASE) + path,
                         headers={"Authorization": f"FlespiToken {self.p.api_token}"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def auth(self):
        return self._get("/devices/all")

    def list_devices(self):
        data = self._get("/devices/all")
        return [{"external_id": str(d["id"]), "name": d.get("name")}
                for d in data.get("result", [])]

    def fetch_positions(self, external_ids):
        ids = ",".join(external_ids)
        data = self._get(f"/devices/{ids}/telemetry/position")
        out = []
        for d in data.get("result", []):
            pos = (d.get("telemetry") or {}).get("position", {}).get("value") or {}
            if pos:
                out.append({"external_id": str(d["id"]),
                            "lat": pos.get("latitude"),
                            "lon": pos.get("longitude"),
                            "speed_kph": pos.get("speed", 0)})
        return out


ADAPTERS = {"wialon": WialonAdapter, "geotab": GeotabAdapter,
            "samsara": SamsaraAdapter, "tracksolid": TrackSolidAdapter,
            "teltonika": TeltonikaAdapter}
