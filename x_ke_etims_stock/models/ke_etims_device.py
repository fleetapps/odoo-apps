# -*- coding: utf-8 -*-
"""A device's other running numbers, and its daily fetch of purchases and imports.

KRA numbers stock movements (sarNo) and confirmed purchases (invcNo of a
purchase) with the trader's own running numbers, separate from sales. Each
device keeps one gapless sequence for each.
"""

import logging

from odoo import api, fields, models

from odoo.addons.x_ke_etims.backends import REGISTRY, EtimsError

_logger = logging.getLogger(__name__)


class KeEtimsDevice(models.Model):
    _inherit = "ke.etims.device"

    stock_sequence_id = fields.Many2one(
        "ir.sequence", string="Stock Movement Numbers", readonly=True, copy=False,
        groups="x_ke_etims.group_ke_etims_manager")
    purchase_sequence_id = fields.Many2one(
        "ir.sequence", string="Purchase Numbers", readonly=True, copy=False,
        groups="x_ke_etims.group_ke_etims_manager")
    last_purchase_fetch = fields.Datetime(readonly=True, copy=False)
    last_import_fetch = fields.Datetime(readonly=True, copy=False)
    can_fetch_purchases = fields.Boolean(compute="_compute_backend_flags")
    can_fetch_imports = fields.Boolean(compute="_compute_backend_flags")

    @api.depends("backend")
    def _compute_backend_flags(self):
        super()._compute_backend_flags()
        for device in self:
            cls = REGISTRY.get(device.backend)
            device.can_fetch_purchases = bool(cls and cls.supports("fetch_purchases"))
            device.can_fetch_imports = bool(cls and cls.supports("fetch_imports"))

    def _ke_etims_own_sequence(self, field, label, code):
        self.ensure_one()
        device = self.sudo()
        if not device[field]:
            device[field] = self.env["ir.sequence"].sudo().create({
                "name": "eTIMS %s: %s" % (label, device.name),
                "code": "ke.etims.%s.%s" % (code, device.id),
                "implementation": "no_gap",
                "padding": 0,
                "number_next": 1,
                "number_increment": 1,
                "company_id": device.company_id.id,
            })
        return device[field]

    def _ke_etims_next_sar_no(self):
        return int(self._ke_etims_own_sequence(
            "stock_sequence_id", "stock movements", "sar").next_by_id())

    def _ke_etims_next_purchase_no(self):
        return int(self._ke_etims_own_sequence(
            "purchase_sequence_id", "purchases", "purchase").next_by_id())

    # ------------------------------------------------------------- fetching

    def action_fetch_purchases(self):
        self.ensure_one()
        try:
            count = self._ke_etims_fetch_purchases()
        except EtimsError as exc:
            return self._ke_etims_notify("eTIMS", str(exc), kind="danger", sticky=True)
        return self._ke_etims_notify("eTIMS", "%s new supplier invoice(s)." % count)

    def action_fetch_imports(self):
        self.ensure_one()
        try:
            count = self._ke_etims_fetch_imports()
        except EtimsError as exc:
            return self._ke_etims_notify("eTIMS", str(exc), kind="danger", sticky=True)
        return self._ke_etims_notify("eTIMS", "%s new import item(s)." % count)

    def _ke_etims_fetch_purchases(self):
        self.ensure_one()
        adapter = self._ke_etims_adapter()
        if not adapter.supports("fetch_purchases"):
            return 0
        started = fields.Datetime.now()
        rows = adapter.fetch_purchases(self.last_purchase_fetch or None)
        purchases = self.env["ke.etims.purchase"].sudo()._ke_etims_store(self, rows)
        purchases._ke_etims_create_bills()
        self.sudo().with_context(ke_etims_keep_state=True).last_purchase_fetch = started
        return len(purchases)

    def _ke_etims_fetch_imports(self):
        self.ensure_one()
        adapter = self._ke_etims_adapter()
        if not adapter.supports("fetch_imports"):
            return 0
        started = fields.Datetime.now()
        rows = adapter.fetch_imports(self.last_import_fetch or None)
        imports = self.env["ke.etims.import"].sudo()._ke_etims_store(self, rows)
        self.sudo().with_context(ke_etims_keep_state=True).last_import_fetch = started
        return len(imports)

    @api.model
    def _cron_fetch_purchases_and_imports(self):
        for device in self._ke_etims_devices():
            for fetch in (device._ke_etims_fetch_purchases, device._ke_etims_fetch_imports):
                try:
                    fetch()
                except EtimsError as exc:
                    _logger.warning("eTIMS fetch failed for %s: %s", device.display_name, exc)
                except Exception:
                    _logger.exception("eTIMS fetch failed for %s", device.display_name)
                if device._ke_etims_can_commit():
                    self.env["ir.cron"]._commit_progress(1)
