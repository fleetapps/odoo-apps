# -*- coding: utf-8 -*-
"""Wizard to import an Access Manager profile bundle (JSON)."""

import base64
import binascii
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccessImportWizard(models.TransientModel):
    _name = "access.manager.import.wizard"
    _description = "Access Manager - Import Profiles"

    file = fields.Binary(string="JSON File", required=True)
    filename = fields.Char(string="File Name")
    profile_count = fields.Integer(string="Profiles in File", readonly=True)

    @api.onchange("file")
    def _onchange_file(self):
        self.profile_count = 0
        if not self.file:
            return
        try:
            data = json.loads(base64.b64decode(self.file))
            self.profile_count = len(data.get("profiles", []))
        except (ValueError, binascii.Error):
            self.profile_count = 0

    def action_import(self):
        self.ensure_one()
        if not self.file:
            raise UserError(_("Please select a JSON file to import."))
        try:
            payload = base64.b64decode(self.file).decode()
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise UserError(_("The file could not be read as JSON: %s", exc))
        created = self.env["access.manager.profile"]._import_bundle(payload)
        return {
            "type": "ir.actions.act_window",
            "name": _("Imported Profiles"),
            "res_model": "access.manager.profile",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }
