# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""A Daraja app: consumer key and secret for one environment, and its token.

The token is cached in the database because Daraja invalidates the previous
token every time a new one is requested. Kept per process, two Odoo workers
would take turns knocking each other's token out; kept per transaction, a
refresh would stay invisible to the others until the request that made it
finished. So a refresh happens under ``SELECT ... FOR UPDATE`` in its own
short transaction that commits at once: one worker asks Daraja, the others
wait a moment and reuse what it got.
"""

import base64
import logging
import threading
from datetime import timedelta

import psycopg2

from odoo import api, fields, models, tools
from odoo.exceptions import UserError
from odoo.modules import module as odoo_module

from ..lib import daraja

_logger = logging.getLogger(__name__)

# Tokens are reused until this close to expiry.
TOKEN_MARGIN = timedelta(seconds=60)
# A token refreshed this recently is reused even when a caller says it was
# rejected: the rejection was of an older token, and refreshing again would
# invalidate the one everybody else is now using.
REFRESH_GRACE = timedelta(seconds=10)

# (dbname, app id) -> (token, expires_at, refreshed_at); naive UTC datetimes.
_TOKENS = {}
_TOKENS_LOCK = threading.Lock()


class MpesaApp(models.Model):
    _name = "mpesa.app"
    _description = "Daraja App"
    _inherit = ["mail.thread"]
    _order = "company_id, environment desc, name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
        tracking=True)
    environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Production")],
        required=True, default="sandbox", tracking=True,
        help="Sandbox and production are separate Daraja apps with separate "
             "keys, passkeys and certificates.")
    consumer_key = fields.Char(groups="x_ke_mpesa.group_mpesa_manager", copy=False)
    consumer_secret = fields.Char(groups="x_ke_mpesa.group_mpesa_manager", copy=False)
    api_url = fields.Char(
        string="API URL", compute="_compute_api_url", store=True, readonly=False,
        groups="x_ke_mpesa.group_mpesa_manager",
        help="Safaricom's URL for the environment. Change it only to go through "
             "a proxy: whoever controls this URL receives the app's secrets.")
    certificate = fields.Text(
        string="Safaricom certificate", groups="x_ke_mpesa.group_mpesa_manager",
        help="The public certificate (PEM) Daraja publishes for this "
             "environment. Initiator passwords are encrypted with it into the "
             "security credential. Sandbox and production certificates differ.")
    access_token = fields.Char(groups="base.group_system", copy=False)
    token_expires_at = fields.Datetime(readonly=True, copy=False)
    token_refreshed_at = fields.Datetime(readonly=True, copy=False)
    shortcode_ids = fields.One2many("mpesa.shortcode", "app_id")
    shortcode_count = fields.Integer(compute="_compute_shortcode_count")

    @api.depends("environment")
    def _compute_api_url(self):
        for app in self:
            app.api_url = (daraja.PRODUCTION_URL if app.environment == "production"
                           else daraja.SANDBOX_URL)

    @api.depends("shortcode_ids")
    def _compute_shortcode_count(self):
        for app in self:
            app.shortcode_count = len(app.shortcode_ids)

    def write(self, vals):
        # New credentials or a new URL make the cached token meaningless.
        if {"consumer_key", "consumer_secret", "api_url", "environment"} & set(vals):
            vals = dict(vals, access_token=False, token_expires_at=False)
            for app in self:
                with _TOKENS_LOCK:
                    _TOKENS.pop((self.env.cr.dbname, app.id), None)
        return super().write(vals)

    # ------------------------------------------------------------------
    # Client and token
    # ------------------------------------------------------------------

    def _client(self):
        """A :class:`DarajaClient` for this app, its token cache wired in."""
        self.ensure_one()
        app = self.sudo()
        if not app.consumer_key or not app.consumer_secret:
            raise UserError(self.env._(
                "The Daraja app %s has no consumer key and secret.", app.display_name))
        return daraja.DarajaClient(
            app.api_url or daraja.SANDBOX_URL,
            app.consumer_key,
            app.consumer_secret,
            token_provider=lambda force=False, rejected=None: app._token(force, rejected),
        )

    @api.model
    def _token_cursor_shared(self):
        """True when the token must be refreshed in the caller's transaction.

        Under tests the app row may only exist in the test's own transaction,
        and a second connection would wait for it forever.
        """
        return bool(tools.config["test_enable"] or odoo_module.current_test)

    def _token(self, force=False, rejected=None):
        """A bearer token for this app, from cache when possible."""
        self.ensure_one()
        now = fields.Datetime.now()
        key = (self.env.cr.dbname, self.id)
        with _TOKENS_LOCK:
            cached = _TOKENS.get(key)
        if cached and self._reusable(cached, now, force, rejected):
            return cached[0]
        if self._token_cursor_shared():
            entry = self._token_refresh(self.env.cr, now, force, rejected)
        else:
            entry = self._token_refresh_isolated(now, force, rejected)
        with _TOKENS_LOCK:
            _TOKENS[key] = entry
        return entry[0]

    @staticmethod
    def _reusable(entry, now, force, rejected):
        token, expires_at, refreshed_at = entry
        if not token or not expires_at:
            return False
        if not force:
            return expires_at - TOKEN_MARGIN > now
        if rejected and token != rejected and expires_at - TOKEN_MARGIN > now:
            return True  # somebody already replaced the rejected token
        return bool(refreshed_at and refreshed_at + REFRESH_GRACE > now
                    and token != rejected)

    def _token_refresh_isolated(self, now, force, rejected):
        """Refresh in a short transaction of its own, committed at once."""
        for _attempt in range(25):
            try:
                with self.env.registry.cursor() as cr:
                    cr.execute("SET LOCAL lock_timeout = '2s'")
                    entry = self._token_refresh(cr, now, force, rejected)
                    if entry is not None:
                        return entry
                    break  # the row is not visible to a new transaction
            except psycopg2.errors.LockNotAvailable:
                continue  # another worker is refreshing: wait and reuse its token
        # The app was created or changed in the caller's uncommitted
        # transaction: refresh there instead.
        return self._token_refresh(self.env.cr, now, force, rejected)

    def _token_refresh(self, cr, now, force, rejected):
        token_fields = ["access_token", "token_expires_at", "token_refreshed_at"]
        if cr is self.env.cr:
            # Raw SQL below: what the ORM holds must reach the table first.
            self.sudo().flush_recordset(token_fields)
        cr.execute(
            "SELECT access_token, token_expires_at, token_refreshed_at "
            "FROM mpesa_app WHERE id = %s FOR UPDATE", [self.id])
        row = cr.fetchone()
        if row is None:
            return None
        if self._reusable(row, now, force, rejected):
            return row
        token, expires_in = self.sudo()._client_unauthenticated().fetch_token()
        expires_at = now + timedelta(seconds=max(expires_in - 1, 60))
        cr.execute(
            "UPDATE mpesa_app SET access_token = %s, token_expires_at = %s, "
            "token_refreshed_at = %s WHERE id = %s",
            [token, expires_at, now, self.id])
        self.invalidate_recordset(token_fields)
        _logger.info("Daraja app %s: new access token (expires %s)", self.id, expires_at)
        return token, expires_at, now

    def _client_unauthenticated(self):
        return daraja.DarajaClient(
            self.api_url or daraja.SANDBOX_URL, self.consumer_key, self.consumer_secret)

    def _forget_token(self):
        for app in self:
            with _TOKENS_LOCK:
                _TOKENS.pop((self.env.cr.dbname, app.id), None)
        self.sudo().write({"access_token": False, "token_expires_at": False})

    # ------------------------------------------------------------------
    # Security credential
    # ------------------------------------------------------------------

    def _encrypt_initiator_password(self, password):
        """Daraja's SecurityCredential: RSA PKCS#1 v1.5 with this app's certificate."""
        self.ensure_one()
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        pem = (self.sudo().certificate or "").strip()
        if not pem:
            raise UserError(self.env._(
                "Paste the Safaricom %(env)s certificate on the Daraja app %(app)s "
                "first: the initiator password is encrypted with it.",
                env=dict(self._fields["environment"].selection)[self.environment],
                app=self.display_name))
        data = pem.encode()
        try:
            if b"BEGIN CERTIFICATE" in data:
                key = x509.load_pem_x509_certificate(data).public_key()
            elif b"BEGIN PUBLIC KEY" in data:
                key = load_pem_public_key(data)
            else:
                key = x509.load_der_x509_certificate(base64.b64decode(data)).public_key()
        except ValueError as exc:
            raise UserError(self.env._(
                "The certificate on %s could not be read: %s", self.display_name, exc)) from exc
        ciphertext = key.encrypt(password.encode(), padding.PKCS1v15())
        return base64.b64encode(ciphertext).decode()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_test_connection(self):
        self.ensure_one()
        self._forget_token()
        try:
            self._token(force=True)
        except (daraja.DarajaError, daraja.DarajaTransportError) as exc:
            raise UserError(self.env._("Daraja refused the credentials: %s", exc)) from exc
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("Daraja accepted the credentials of %s.",
                                      self.display_name),
            },
        }

    def action_open_shortcodes(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "x_ke_mpesa.action_mpesa_shortcode")
        action["domain"] = [("app_id", "=", self.id)]
        action["context"] = {"default_app_id": self.id,
                             "default_company_id": self.company_id.id}
        return action
