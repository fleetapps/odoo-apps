# -*- coding: utf-8 -*-
"""Every exchange with KRA or a provider, with its secrets taken out.

The request and response are kept as sent and received, less anything under a
secret-looking key and less any known secret string wherever else it appears
(see ``backends.base.redact``). Only eTIMS managers can read them. The
document's chatter gets a one-line note per exchange -- endpoint, result,
time -- so an accountant can follow what happened without seeing payloads.
"""

import json

from markupsafe import Markup

from odoo import _, api, fields, models

#: Payloads beyond this are cut, so one runaway response cannot bloat the table.
MAX_TEXT = 60000


def _dump(value):
    if value is None:
        return False
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, indent=1, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n... (cut)"


class KeEtimsLog(models.Model):
    _name = "ke.etims.log"
    _description = "eTIMS Exchange Log"
    _order = "id desc"

    device_id = fields.Many2one("ke.etims.device", required=True, index=True,
                                ondelete="cascade")
    company_id = fields.Many2one(related="device_id.company_id", store=True, index=True)
    document_id = fields.Many2one("ke.etims.document", index="btree_not_null",
                                  ondelete="cascade")
    endpoint = fields.Char(required=True)
    http_status = fields.Integer(string="HTTP Status")
    result_code = fields.Char(compute="_compute_result_code", store=True)
    duration_ms = fields.Integer(string="Duration (ms)")
    request = fields.Text()
    response = fields.Text()
    error = fields.Text()

    @api.depends("response")
    def _compute_result_code(self):
        for log in self:
            code = False
            try:
                data = json.loads(log.response) if log.response else None
            except ValueError:
                data = None
            if isinstance(data, dict):
                code = data.get("resultCd") or data.get("status") or False
            log.result_code = str(code) if code else False

    @api.depends("endpoint", "create_date")
    def _compute_display_name(self):
        for log in self:
            log.display_name = "%s %s" % (log.endpoint, log.create_date or "")

    @api.model
    def _ke_etims_record(self, device, document, *, endpoint, request=None, response=None,
                         status=None, error=None, duration_ms=None):
        log = self.sudo().create({
            "device_id": device.id,
            "document_id": document.id if document else False,
            "endpoint": endpoint,
            "http_status": status or 0,
            "duration_ms": duration_ms or 0,
            "request": _dump(request),
            "response": _dump(response),
            "error": error or False,
        })
        if document:
            outcome = error or log.result_code or (status and "HTTP %s" % status) or _("done")
            document.sudo().message_post(
                body=Markup("eTIMS <code>%s</code>: %s%s") % (
                    endpoint, outcome,
                    Markup(" (%s ms)") % duration_ms if duration_ms else ""),
                message_type="comment", subtype_xmlid="mail.mt_note")
        return log
