# -*- coding: utf-8 -*-
"""One eTIMS device: a PIN, a branch, a backend and its credentials.

KRA thinks in branches. Every branch of a taxpayer (``bhfId``, "00" for head
office) has its own control unit, its own communication key and its own run of
invoice numbers, and a sale signed under one branch is that branch's sale. So a
device belongs to one Odoo company -- the company itself, or one of its branch
companies -- and carries everything needed to sign for it.

Invoice numbers
---------------
KRA expects each branch's invoice numbers to start at 1 and increase by exactly
one, with no gaps. Odoo's own invoice names cannot serve: they are per journal,
a credit note has a different sequence, and a cancelled draft leaves a hole.
So each device has a gapless sequence of its own, and a number is drawn from
it only at the moment a document is about to be sent, under a lock on the
device row. See ``ke.etims.document`` for why a number is never drawn early
and when one can be handed back.

The production gate
-------------------
A device cannot be set to Production without a certification reference: for
the KRA backends, KRA's certificate for the integration (Odin's third-party
vendor certificate or the client's self-integration certificate); for DigiTax,
the record of DigiTax's own KRA certification. The demo backend never leaves
Demo, and the other backends never use it.
"""

import logging
import re
import secrets as pysecrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..backends import REGISTRY, EtimsError, Operator, get_adapter_class

_logger = logging.getLogger(__name__)

PIN_RE = re.compile(r"^[A-Z]\d{9}[A-Z]$")
BRANCH_RE = re.compile(r"^\d{2}$")

ENVIRONMENTS = [("demo", "Demo"), ("test", "Test"), ("production", "Production")]


def backend_selection(self=None):
    options = []
    for code, cls in REGISTRY.items():
        label = cls.label
        if cls.experimental:
            label += " (experimental)"
        if not cls.is_available():
            label += " (not installed)"
        options.append((code, label))
    order = ["demo", "digitax", "kra_direct", "kra_etims_lib"]
    return sorted(options, key=lambda item: order.index(item[0]) if item[0] in order else 99)


class KeEtimsDevice(models.Model):
    _name = "ke.etims.device"
    _description = "eTIMS Device"
    _inherit = ["mail.thread"]
    _order = "company_id, bhf_id, id"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company,
        help="The company, or branch company, this device signs for.")
    backend = fields.Selection(
        selection=backend_selection, required=True, tracking=True,
        default=lambda self: self.env.company.ke_etims_default_backend or "demo")
    environment = fields.Selection(
        ENVIRONMENTS, required=True, tracking=True,
        default=lambda self: self.env.company.ke_etims_environment or "demo")
    experimental = fields.Boolean(compute="_compute_backend_flags")
    backend_available = fields.Boolean(compute="_compute_backend_flags")
    backend_note = fields.Char(compute="_compute_backend_flags")

    tin = fields.Char(string="KRA PIN", required=True, tracking=True,
                      default=lambda self: self.env.company.vat)
    bhf_id = fields.Char(string="Branch ID", required=True, size=2, tracking=True,
                         default=lambda self: self.env.company.ke_etims_bhf_id or "00",
                         help="KRA's branch ID: 00 for head office, 01.. for branches.")
    serial = fields.Char(string="Device Serial", tracking=True,
                         help="The OSCU serial number KRA approved for this branch.")
    cmc_key = fields.Char(string="Communication Key", copy=False,
                          groups="x_ke_etims.group_ke_etims_manager",
                          help="Issued by KRA when the device is initialised.")
    api_key = fields.Char(string="API Key", copy=False,
                          groups="x_ke_etims.group_ke_etims_manager",
                          help="The provider's key: for DigiTax, the TEST key in Test "
                               "and the LIVE key in Production.")
    callback_token = fields.Char(
        copy=False, groups="x_ke_etims.group_ke_etims_manager",
        default=lambda self: pysecrets.token_urlsafe(24),
        help="Makes this device's callback address unguessable. Callbacks are "
             "never trusted on their own: the record is re-read from the "
             "provider before anything changes.")
    base_url = fields.Char(string="Base URL Override",
                           groups="x_ke_etims.group_ke_etims_manager",
                           help="Leave empty to use the backend's own address.")
    certification_ref = fields.Char(
        string="Certification", tracking=True,
        help="Required for Production. For the KRA backends, the certificate "
             "KRA issued for this integration (third-party vendor or "
             "self-integration). For DigiTax, the reference of DigiTax's KRA "
             "certification.")

    state = fields.Selection(
        [("draft", "Not initialised"), ("initialised", "Initialised"), ("error", "Error")],
        default="draft", required=True, tracking=True, copy=False)
    last_init = fields.Datetime(string="Initialised On", readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False)
    scu_id = fields.Char(string="SCU ID", readonly=True, copy=False)
    mrc_no = fields.Char(string="MRC Number", readonly=True, copy=False)
    device_ref = fields.Char(string="Device ID", readonly=True, copy=False)
    taxpayer_name = fields.Char(readonly=True, copy=False)
    branch_name = fields.Char(readonly=True, copy=False)

    sequence_id = fields.Many2one(
        "ir.sequence", string="KRA Invoice Numbers", readonly=True, copy=False,
        groups="x_ke_etims.group_ke_etims_manager")
    next_invoice_no = fields.Integer(
        compute="_compute_next_invoice_no", inverse="_inverse_next_invoice_no",
        groups="x_ke_etims.group_ke_etims_manager",
        help="The KRA invoice number the next sale or credit note will take. "
             "Change it only to continue a branch's numbering from another "
             "system, from the last number KRA accepted plus one.")
    responsible_user_id = fields.Many2one(
        "res.users", string="Responsible",
        default=lambda self: self.env.user,
        help="Gets an activity when a document fails and its invoice has no salesperson.")

    last_code_sync = fields.Datetime(readonly=True, copy=False)
    last_item_class_sync = fields.Datetime(readonly=True, copy=False)
    last_notice_sync = fields.Datetime(readonly=True, copy=False)

    document_ids = fields.One2many("ke.etims.document", "device_id")
    document_queued_count = fields.Integer(compute="_compute_document_counts")
    document_failed_count = fields.Integer(compute="_compute_document_counts")
    blocking_document_id = fields.Many2one(
        "ke.etims.document", compute="_compute_document_counts",
        help="A document holding a KRA invoice number that is neither signed "
             "nor released. Later documents of this device wait for it.")

    can_sync_codes = fields.Boolean(compute="_compute_backend_flags")
    can_fetch_branches = fields.Boolean(compute="_compute_backend_flags")
    can_fetch_notices = fields.Boolean(compute="_compute_backend_flags")
    callback_url = fields.Char(compute="_compute_callback_url",
                               groups="x_ke_etims.group_ke_etims_manager")

    _company_branch_unique = models.UniqueIndex(
        "(company_id, bhf_id) WHERE active IS TRUE",
        "A company already has an active eTIMS device for this branch ID.")

    # ------------------------------------------------------------ computes

    @api.depends("company_id", "bhf_id", "backend", "environment")
    def _compute_name(self):
        labels = dict(ENVIRONMENTS)
        for device in self:
            backend = dict(backend_selection()).get(device.backend, device.backend)
            device.name = "%s / %s - %s (%s)" % (
                device.company_id.name or "", device.bhf_id or "??", backend,
                labels.get(device.environment, ""))

    @api.depends("backend")
    def _compute_backend_flags(self):
        for device in self:
            cls = REGISTRY.get(device.backend)
            device.experimental = bool(cls and cls.experimental)
            device.backend_available = bool(cls and cls.is_available())
            notes = []
            if cls and cls.experimental:
                notes.append(_("Experimental: built on an alpha library not yet "
                               "verified against KRA's live sandbox."))
            if cls and not cls.is_available():
                notes.append(cls.unavailable_reason() or _("Not installed."))
            device.backend_note = " ".join(notes) or False
            device.can_sync_codes = bool(cls and cls.supports("sync_codes"))
            device.can_fetch_branches = bool(cls and cls.supports("fetch_branches"))
            device.can_fetch_notices = bool(cls and cls.supports("fetch_notices"))

    def _compute_callback_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for device in self:
            device.callback_url = "%s/x_ke_etims/digitax/%s/%s" % (
                base.rstrip("/"), device.id, device.sudo().callback_token) \
                if device.id and device.backend == "digitax" else False

    def _compute_next_invoice_no(self):
        for device in self:
            device.next_invoice_no = device.sudo().sequence_id.number_next_actual or 1

    def _inverse_next_invoice_no(self):
        for device in self:
            if device.next_invoice_no < 1:
                raise ValidationError(_("KRA invoice numbers start at 1."))
            device.sudo()._ke_etims_sequence().number_next_actual = device.next_invoice_no

    def _compute_document_counts(self):
        Document = self.env["ke.etims.document"].sudo()
        for device in self:
            device.document_queued_count = Document.search_count(
                [("device_id", "=", device.id), ("state", "in", ("to_send", "sending", "pending"))])
            device.document_failed_count = Document.search_count(
                [("device_id", "=", device.id), ("state", "=", "failed")])
            device.blocking_document_id = device._ke_etims_blocking_document()

    # ---------------------------------------------------------- constraints

    @api.constrains("tin", "bhf_id", "company_id", "backend", "environment",
                    "certification_ref")
    def _check_device(self):
        for device in self:
            if device.company_id.account_fiscal_country_id.code != "KE":
                raise ValidationError(_(
                    "eTIMS devices are for Kenyan companies only; %(company)s "
                    "does not account in Kenya.", company=device.company_id.name))
            if device.tin and not PIN_RE.match(device.tin):
                raise ValidationError(_(
                    "%(pin)s is not a KRA PIN: one letter, nine digits and a "
                    "letter, e.g. P051234567A.", pin=device.tin))
            if not BRANCH_RE.match(device.bhf_id or ""):
                raise ValidationError(_(
                    "A KRA branch ID is two digits: 00 for head office, 01 "
                    "upwards for branches."))
            cls = REGISTRY.get(device.backend)
            if cls and device.environment not in cls.environments:
                if device.backend == "demo":
                    raise ValidationError(_("The demo backend only runs in the Demo environment."))
                raise ValidationError(_(
                    "%(backend)s talks to real servers; choose Test or "
                    "Production, or use the demo backend for Demo.",
                    backend=cls.label))
            if device.environment == "production" and not device.certification_ref:
                raise ValidationError(_(
                    "Production is blocked until the device records its "
                    "certification: KRA's certificate for this integration "
                    "(third-party vendor or self-integration), or, for DigiTax, "
                    "the reference of DigiTax's KRA certification."))

    # -------------------------------------------------------------- crud

    @api.model_create_multi
    def create(self, vals_list):
        # The one-active-device-per-branch index reads "active": a device
        # archived earlier in this transaction must be on disk first.
        self.flush_model(["active", "company_id", "bhf_id"])
        devices = super().create(vals_list)
        for device in devices:
            device.sudo()._ke_etims_sequence()
        return devices

    def write(self, vals):
        identity = {"tin", "bhf_id", "backend", "environment", "serial", "base_url",
                    "company_id"}
        if identity & set(vals) and not self.env.context.get("ke_etims_keep_state"):
            # A device that now points somewhere else has to be initialised
            # against it; keeping "initialised" would sign with the old key.
            vals.setdefault("state", "draft")
        return super().write(vals)

    def _ke_etims_sequence(self):
        self.ensure_one()
        if not self.sequence_id:
            self.sequence_id = self.env["ir.sequence"].sudo().create({
                "name": "eTIMS invoice numbers: %s" % self.name,
                "code": "ke.etims.invoice.%s" % self.id,
                "implementation": "no_gap",
                "padding": 0,
                "number_next": 1,
                "number_increment": 1,
                "company_id": self.company_id.id,
            })
        return self.sequence_id

    # ------------------------------------------------------------ adapter

    def _ke_etims_adapter(self, document=None, timeout=None):
        """The backend for this device, recording every exchange as a log."""
        self.ensure_one()
        cls = get_adapter_class(self.backend)
        if not cls.is_available():
            raise UserError(cls.unavailable_reason() or _("This backend is not available."))
        Log = self.env["ke.etims.log"].sudo()
        # The adapter reads credentials and the base URL, which only eTIMS
        # managers may see; an accountant pressing "Send now" still sends.
        device = self.sudo()

        def recorder(**exchange):
            Log._ke_etims_record(device, document, **exchange)

        return cls(device, recorder=recorder, timeout=timeout)

    def _ke_etims_operator(self):
        user = self.env.user
        return Operator(id=(user.login or str(user.id))[:20], name=(user.name or "Odoo")[:60])

    def _ke_etims_tax_rates(self):
        """Tax-type letter to rate, from KRA's code class 04."""
        codes = self.env["ke.etims.code"].sudo().with_context(active_test=False).search(
            [("code_class", "=", "04")])
        return {code.code: code.rate for code in codes}

    # ------------------------------------------------------------ actions

    def _ke_etims_notify(self, title, message, kind="success", sticky=False):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": title, "message": message, "type": kind, "sticky": sticky,
                       "next": {"type": "ir.actions.act_window_close"}},
        }

    def _ke_etims_fail(self, error):
        """Record a failed device action, keep its logs, and say so.

        The action returns a notification instead of raising: raising would
        roll back the transaction and take the exchange logs with it, which
        are exactly what is needed to see why initialisation failed.
        """
        self.write({"state": "error", "last_error": str(error)})
        return self._ke_etims_notify(_("eTIMS"), str(error), kind="danger", sticky=True)

    def action_initialize(self):
        self.ensure_one()
        self._ke_etims_check_company()
        try:
            info = self._ke_etims_adapter().initialize()
        except EtimsError as exc:
            return self._ke_etims_fail(exc)
        vals = {
            "state": "initialised",
            "last_init": fields.Datetime.now(),
            "last_error": False,
            "scu_id": info.scu_id,
            "mrc_no": info.mrc_no,
            "device_ref": info.device_id,
            "taxpayer_name": info.taxpayer_name,
            "branch_name": info.branch_name,
        }
        if info.cmc_key:
            vals["cmc_key"] = info.cmc_key
        self.sudo().with_context(ke_etims_keep_state=True).write(vals)
        self.message_post(body=_("Device initialised with %(backend)s (%(env)s).",
                                 backend=dict(backend_selection()).get(self.backend),
                                 env=dict(ENVIRONMENTS).get(self.environment)))
        # Reference data is needed before anything can be signed, so fetch it
        # now; a failure here does not undo the initialisation.
        self._ke_etims_sync_reference(raise_errors=False)
        return self._ke_etims_notify(_("eTIMS"), _("The device is initialised."))

    def action_sync_codes(self):
        self.ensure_one()
        try:
            self._ke_etims_sync_reference(raise_errors=True)
        except EtimsError as exc:
            return self._ke_etims_notify(_("eTIMS"), str(exc), kind="danger", sticky=True)
        return self._ke_etims_notify(_("eTIMS"), _("KRA codes and item classes are up to date."))

    def _ke_etims_sync_reference(self, raise_errors=False):
        """Codes, item classes and notices, each where the backend has them."""
        self.ensure_one()
        adapter = self._ke_etims_adapter()
        now = fields.Datetime.now()
        steps = [
            ("sync_codes", "last_code_sync", "ke.etims.code"),
            ("sync_item_classes", "last_item_class_sync", "ke.etims.item.class"),
            ("fetch_notices", "last_notice_sync", "ke.etims.notice"),
        ]
        for operation, stamp, model in steps:
            if not adapter.supports(operation):
                continue
            try:
                # Codes and classes are always fetched in full: they are small,
                # and an incremental sync that misses a deletion is worse than
                # a few kilobytes. Notices are fetched since the last time.
                since = (self[stamp] or None) if operation == "fetch_notices" else None
                rows = getattr(adapter, operation)(since)
            except EtimsError as exc:
                _logger.warning("eTIMS %s failed for %s: %s", operation, self.display_name, exc)
                if raise_errors:
                    raise
                continue
            self.env[model].sudo()._ke_etims_store(rows)
            self.sudo().with_context(ke_etims_keep_state=True).write({stamp: now})
        self.env["account.tax"].sudo()._ke_etims_fill_tax_types()

    def action_fetch_branches(self):
        self.ensure_one()
        try:
            rows = self._ke_etims_adapter().fetch_branches()
        except EtimsError as exc:
            return self._ke_etims_notify(_("eTIMS"), str(exc), kind="danger", sticky=True)
        Branch = self.env["ke.etims.branch"].sudo()
        root = self.company_id.root_id
        for row in rows:
            if not row.get("bhf_id"):
                continue
            branch = Branch.search([("company_id", "=", root.id),
                                    ("bhf_id", "=", row["bhf_id"])], limit=1)
            vals = {"name": row.get("name"), "status": row.get("status"),
                    "is_head_office": row.get("is_head_office")}
            if branch:
                branch.write(vals)
            else:
                linked = self.env["res.company"].search(
                    [("id", "child_of", root.id), ("ke_etims_bhf_id", "=", row["bhf_id"])],
                    limit=1)
                Branch.create({**vals, "company_id": root.id, "bhf_id": row["bhf_id"],
                               "linked_company_id": linked.id})
        action = self.env["ir.actions.act_window"]._for_xml_id("x_ke_etims.action_ke_etims_branch")
        action["domain"] = [("company_id", "=", root.id)]
        return action

    def action_view_documents(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("x_ke_etims.action_ke_etims_document")
        action["domain"] = [("device_id", "=", self.id)]
        action["context"] = {"search_default_group_state": 1}
        return action

    def action_view_logs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("x_ke_etims.action_ke_etims_log")
        action["domain"] = [("device_id", "=", self.id)]
        return action

    def action_process_queue(self):
        self.ensure_one()
        self.env["ke.etims.document"]._ke_etims_process_queue(devices=self)
        return True

    # ------------------------------------------------------------ numbers

    def _ke_etims_lock(self):
        """Lock this device's row for the rest of the transaction, or return
        False if someone else holds it. NO KEY UPDATE, so the logs and
        documents that reference the device can still be written."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    "SELECT id FROM ke_etims_device WHERE id = %s FOR NO KEY UPDATE NOWAIT",
                    [self.id])
        except Exception:  # psycopg2.errors.LockNotAvailable
            return False
        return True

    def _ke_etims_next_invoice_no(self):
        self.ensure_one()
        return int(self.sudo()._ke_etims_sequence().next_by_id())

    def _ke_etims_release_invoice_no(self, number):
        """Hand a number back, if it is the last one drawn.

        Only ever called for a number KRA definitely did not record, and only
        works for the most recent one, which is the only one that can be
        unsigned on a device that sends in order. Anything else would leave a
        gap or reuse a number KRA already has.
        """
        self.ensure_one()
        sequence = self.sudo()._ke_etims_sequence()
        if number and sequence.number_next_actual == number + 1:
            sequence.number_next_actual = number
            return True
        return False

    def _ke_etims_blocking_document(self):
        """The numbered but unsigned document every later one must wait for."""
        self.ensure_one()
        if not REGISTRY.get(self.backend) or not REGISTRY[self.backend].strict_sequence:
            return self.env["ke.etims.document"]
        return self.env["ke.etims.document"].sudo().search([
            ("device_id", "=", self.id),
            ("kra_invoice_no", "!=", False),
            ("state", "in", ("to_send", "sending", "failed")),
            ("kind", "in", ("sale", "credit_note")),
        ], order="kra_invoice_no", limit=1)

    # ------------------------------------------------------------- helpers

    def _ke_etims_check_company(self):
        for device in self:
            if device.company_id.account_fiscal_country_id.code != "KE":
                raise UserError(_("eTIMS is only available to Kenyan companies."))

    @api.model
    def _ke_etims_devices(self, companies=None):
        """Initialised devices of Kenyan companies -- the only ones any cron touches."""
        domain = [("state", "=", "initialised"),
                  ("company_id.account_fiscal_country_id.code", "=", "KE")]
        if companies is not None:
            domain.append(("company_id", "in", companies.ids))
        return self.sudo().search(domain)

    @api.model
    def _cron_sync_reference(self):
        for device in self._ke_etims_devices():
            try:
                device._ke_etims_sync_reference(raise_errors=False)
            except Exception:
                _logger.exception("eTIMS reference sync failed for %s", device.display_name)
            if device._ke_etims_can_commit():
                self.env["ir.cron"]._commit_progress(1)

    def _ke_etims_can_commit(self):
        return self.env["ke.etims.document"]._ke_etims_can_commit()
