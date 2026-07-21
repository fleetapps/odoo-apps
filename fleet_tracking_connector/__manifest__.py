# Manifest: https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
{
    "name": "Fleet GPS Tracking Connector",
    "version": "19.0.1.0.0",
    "category": "Human Resources/Fleet",
    "summary": "Live GPS tracking inside Odoo Fleet: Wialon, Teltonika, "
               "TrackSolid, Geotab and Samsara. Positions, trips, odometer "
               "sync, speeding & geofence alerts.",
    "author": "YourCompany",
    "license": "OPL-1",
    "price": 349.00,
    "currency": "USD",
    "depends": ["fleet", "mail"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/tracking_views.xml",
    ],
    "installable": True,
    "application": True,
}
