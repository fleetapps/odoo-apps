# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Tills, stores and paybills: one shortcode, one bank journal.

Two things on this model are deliberately configurable rather than derived,
because Safaricom's own sources disagree and only a live KES 1 test settles
them:

* STK Push for a till: ``BusinessShortCode`` defaults to the parent store
  (the number used at Daraja go-live) and ``PartyB`` to the till, per the
  text of Daraja's error 2028. Both are editable.
* C2B registration: whether a till can be registered on its own or only
  through its store. ``c2b_shortcode`` is the number registered, and the
  per-shortcode callback token tells us which registration a confirmation
  came through even when its body cannot say which till was paid.
"""

import logging
import re
import secrets
from urllib.parse import urlsplit

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.http import db_list

from ..lib import daraja

_logger = logging.getLogger(__name__)

# Safaricom refuses (or silently never calls) URLs containing these, in any case.
FORBIDDEN_URL_WORDS = ("m-pesa", "mpesa", "safaricom", "exe", "exec", "cmd", "sql", "query")

CALLBACK_PATHS = {
    "c2b_confirm": "c2b/confirm",
    "c2b_validate": "c2b/validate",
    "stk": "stk",
    "status_result": "status/result",
    "status_timeout": "status/timeout",
    "pull": "pull",
    "b2c_result": "b2c/result",
    "b2c_timeout": "b2c/timeout",
    "reversal_result": "reversal/result",
    "reversal_timeout": "reversal/timeout",
    "balance_result": "balance/result",
    "balance_timeout": "balance/timeout",
}


def _new_token(_records=None):
    # Hexadecimal cannot spell any of FORBIDDEN_URL_WORDS: x, m, p, s, q, l
    # and y are not hex digits.
    return secrets.token_hex(16)


class MpesaShortcode(models.Model):
    _name = "mpesa.shortcode"
    _description = "M-Pesa Shortcode"
    _inherit = ["mail.thread"]
    _order = "company_id, parent_path, sequence, number"
    _parent_store = True
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True, readonly=False)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    kind = fields.Selection(
        [("till", "Till (Buy Goods)"), ("store", "Store / Head office"),
         ("paybill", "Paybill")],
        required=True, default="till", tracking=True)
    number = fields.Char(required=True, tracking=True)
    parent_id = fields.Many2one(
        "mpesa.shortcode", string="Store", index=True, ondelete="restrict",
        domain="[('kind', '=', 'store')]",
        help="The store (head office) number a till sits under. Its passkey "
             "and, by default, its number are used for STK Push.")
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("mpesa.shortcode", "parent_id", string="Tills")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
        tracking=True,
        help="The company or branch this till's money belongs to.")
    currency_id = fields.Many2one(related="company_id.currency_id")
    app_id = fields.Many2one("mpesa.app", string="Daraja app", check_company=True,
                             tracking=True)
    state = fields.Selection(
        [("sandbox", "Sandbox"), ("live", "Live")], required=True,
        default="sandbox", tracking=True)

    passkey = fields.Char(
        groups="x_ke_mpesa.group_mpesa_manager", copy=False,
        help="The Lipa na M-PESA Online passkey. Safaricom issues it per store "
             "or paybill at go-live; a till without one uses its store's.")
    has_passkey = fields.Boolean(compute="_compute_has_passkey")

    # STK Push
    stk_enabled = fields.Boolean("STK Push", default=True)
    stk_business_shortcode = fields.Char(
        "STK BusinessShortCode", compute="_compute_stk_defaults", store=True,
        readonly=False,
        help="Sent as BusinessShortCode and used in the STK password. For a "
             "till: the store number used at go-live.")
    stk_party_b = fields.Char(
        "STK PartyB", compute="_compute_stk_defaults", store=True, readonly=False,
        help="The account credited. For a till: the till number.")
    stk_transaction_type = fields.Selection(
        [("CustomerBuyGoodsOnline", "CustomerBuyGoodsOnline (till)"),
         ("CustomerPayBillOnline", "CustomerPayBillOnline (paybill)")],
        "STK transaction type", compute="_compute_stk_defaults", store=True,
        readonly=False)
    stk_query_delay = fields.Integer(
        "Ask M-PESA after (s)", default=90,
        help="No callback by then: STK Query is asked, and its answer is final.")
    stk_confirm_threshold = fields.Monetary(
        "Double-check STK above", currency_field="currency_id",
        help="A successful STK callback above this amount is confirmed with "
             "STK Query before the payment counts. 0 = never.")

    # C2B
    c2b_shortcode = fields.Char(
        "C2B registration shortcode", compute="_compute_c2b_shortcode", store=True,
        readonly=False,
        help="The number whose confirmation URL is registered: the till, its "
             "store or the paybill, whichever Safaricom accepts for C2B.")
    callback_token = fields.Char(
        default=_new_token, required=True, copy=False, readonly=True,
        groups="x_ke_mpesa.group_mpesa_officer")
    c2b_validation = fields.Boolean(
        "External validation",
        help="Off by default; Safaricom must enable it on the shortcode. When on, "
             "M-PESA asks before completing each payment and waits about 8 seconds.")
    c2b_response_type = fields.Selection(
        [("Completed", "Completed"), ("Cancelled", "Cancelled")],
        "If validation is unreachable", default="Completed", required=True)
    c2b_registered_at = fields.Datetime("C2B URLs registered", readonly=True, copy=False)
    c2b_confirmation_url = fields.Char(compute="_compute_urls",
                                       groups="x_ke_mpesa.group_mpesa_officer")
    c2b_validation_url = fields.Char(compute="_compute_urls",
                                     groups="x_ke_mpesa.group_mpesa_officer")
    stk_callback_url = fields.Char(compute="_compute_urls",
                                   groups="x_ke_mpesa.group_mpesa_officer")
    url_problems = fields.Text(compute="_compute_urls")

    # Initiator (Transaction Status, Pull registration, B2C, reversals)
    initiator_name = fields.Char(groups="x_ke_mpesa.group_mpesa_manager", copy=False)
    initiator_password = fields.Char(
        compute="_compute_initiator_password", inverse="_inverse_initiator_password",
        groups="x_ke_mpesa.group_mpesa_manager",
        help="Never stored: encrypted on save into the security credential.")
    security_credential = fields.Text(
        groups="x_ke_mpesa.group_mpesa_manager", copy=False,
        help="The initiator password encrypted with the Safaricom certificate "
             "of the app's environment. You may also paste one generated on "
             "the Daraja portal.")

    # Accounting
    journal_id = fields.Many2one(
        "account.journal", check_company=True, domain="[('type', '=', 'bank')]",
        tracking=True, help="The bank journal whose statement lines are this "
                            "shortcode's receipts.")
    analytic_account_id = fields.Many2one(
        "account.analytic.account", string="Outlet", check_company=True)

    # People and hours
    user_ids = fields.Many2many(
        "res.users", "mpesa_shortcode_user_rel", "shortcode_id", "user_id",
        string="Outlet staff",
        help="Users who see this till's receipts (M-Pesa: Outlet role).")
    supervisor_id = fields.Many2one(
        "res.users", string="Supervisor",
        help="Alerted when a manually entered receipt is not confirmed by M-PESA.")
    trading_hour_from = fields.Float("Trading from", default=7.0)
    trading_hour_to = fields.Float("Trading to", default=20.0)

    # Health
    last_callback_at = fields.Datetime(readonly=True, copy=False)
    last_org_balance = fields.Monetary(
        "Last M-PESA balance", currency_field="currency_id", readonly=True, copy=False,
        help="OrgAccountBalance of the latest C2B confirmation.")
    last_org_balance_at = fields.Datetime(readonly=True, copy=False)
    transaction_count = fields.Integer(compute="_compute_transaction_count")

    _number_company_uniq = models.Constraint(
        "unique(company_id, number, kind)",
        "This shortcode is already set up for this company.",
    )
    _token_uniq = models.Constraint(
        "unique(callback_token)", "Callback tokens must be unique.")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("kind", "number", "analytic_account_id", "company_id")
    def _compute_name(self):
        labels = dict(self._fields["kind"].selection)
        for rec in self:
            kind = labels.get(rec.kind, "").split(" (")[0]
            where = rec.analytic_account_id.name or rec.company_id.name or ""
            rec.name = f"{kind} {rec.number or ''}" + (f" - {where}" if where else "")

    def _compute_has_passkey(self):
        for rec in self:
            rec.has_passkey = bool(rec.sudo()._passkey())

    @api.depends("kind", "number", "parent_id.number")
    def _compute_stk_defaults(self):
        for rec in self:
            if rec.kind == "paybill":
                rec.stk_business_shortcode = rec.number
                rec.stk_party_b = rec.number
                rec.stk_transaction_type = "CustomerPayBillOnline"
            else:
                rec.stk_business_shortcode = (
                    rec.parent_id.number if rec.kind == "till" and rec.parent_id
                    else rec.number)
                rec.stk_party_b = rec.number
                rec.stk_transaction_type = "CustomerBuyGoodsOnline"

    @api.depends("number")
    def _compute_c2b_shortcode(self):
        for rec in self:
            rec.c2b_shortcode = rec.c2b_shortcode or rec.number

    @api.depends("callback_token")
    def _compute_urls(self):
        for rec in self:
            sudo = rec.sudo()
            rec.c2b_confirmation_url = sudo._callback_url("c2b_confirm")
            rec.c2b_validation_url = sudo._callback_url("c2b_validate")
            rec.stk_callback_url = sudo._callback_url("stk")
            rec.url_problems = "\n".join(sudo._url_problems(rec.c2b_confirmation_url)) or False

    def _compute_initiator_password(self):
        for rec in self:
            rec.initiator_password = False

    def _inverse_initiator_password(self):
        for rec in self:
            if rec.initiator_password:
                if not rec.app_id:
                    raise UserError(self.env._("Choose the Daraja app first: its "
                                               "certificate encrypts the password."))
                rec.security_credential = rec.app_id._encrypt_initiator_password(
                    rec.initiator_password)
        # Not even the cache keeps the password once it is encrypted.
        self.invalidate_recordset(["initiator_password"])

    def _compute_transaction_count(self):
        groups = self.env["mpesa.transaction"]._read_group(
            [("shortcode_id", "in", self.ids)], ["shortcode_id"], ["__count"])
        counts = {shortcode.id: count for shortcode, count in groups}
        for rec in self:
            rec.transaction_count = counts.get(rec.id, 0)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains("number")
    def _check_number(self):
        for rec in self:
            if not re.fullmatch(r"\d{4,9}", rec.number or ""):
                raise ValidationError(self.env._(
                    "A shortcode is 4 to 9 digits, not %r.", rec.number))

    @api.constrains("kind", "parent_id")
    def _check_parent(self):
        for rec in self:
            if rec.parent_id and rec.kind != "till":
                raise ValidationError(self.env._("Only a till sits under a store."))

    @api.constrains("state", "app_id")
    def _check_live_app(self):
        for rec in self:
            if rec.state == "live" and rec.app_id and rec.app_id.environment != "production":
                raise ValidationError(self.env._(
                    "%s is live but its Daraja app is a sandbox app.", rec.display_name))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _passkey(self):
        """The passkey that signs this shortcode's STK Push: its own or its store's."""
        self.ensure_one()
        return self.passkey or self.parent_id.passkey or ""

    def _family(self):
        """The shortcodes M-PESA may report this one's payments under: a till's
        store, a store's tills. Sibling tills are *not* family: a receipt paid
        to the till next door is a cross-till receipt."""
        return self.parent_id | self.child_ids

    def _app(self):
        self.ensure_one()
        app = self.app_id or self.parent_id.app_id
        if not app:
            raise UserError(self.env._("%s has no Daraja app.", self.display_name))
        return app

    def _client(self):
        return self._app()._client()

    @api.model
    def _callback_base_url(self):
        params = self.env["ir.config_parameter"].sudo()
        return (params.get_param("x_ke_mpesa.callback_base_url")
                or params.get_param("web.base.url") or "").rstrip("/")

    def _callback_url(self, kind):
        """The public URL Safaricom calls for ``kind`` on this shortcode."""
        self.ensure_one()
        if not self.callback_token:
            return False
        path = CALLBACK_PATHS[kind]
        return f"{self._callback_base_url()}/pay/ke/{self.callback_token}/{path}"

    @api.model
    def _url_problems(self, url):
        """Why Safaricom would refuse ``url``, as sentences. Empty when fine."""
        if not url:
            return [self.env._("No callback URL: set web.base.url.")]
        problems = []
        parts = urlsplit(url)
        lowered = url.lower()
        for word in FORBIDDEN_URL_WORDS:
            if word in lowered:
                problems.append(self.env._(
                    "The URL contains “%s”, which Safaricom does not allow anywhere "
                    "in a callback URL. Use another host name (set the system "
                    "parameter x_ke_mpesa.callback_base_url).", word))
        if parts.scheme != "https":
            problems.append(self.env._("Production callbacks must be HTTPS."))
        if parts.port not in (None, 443):
            problems.append(self.env._("Callbacks must arrive on port 443."))
        host = (parts.hostname or "").lower()
        if host in ("localhost", "127.0.0.1") or host.endswith((".local", ".lan")):
            problems.append(self.env._("%s is not reachable from Safaricom.", host))
        if any(tunnel in host for tunnel in ("ngrok", "mockbin", "requestbin")):
            problems.append(self.env._("Safaricom blocks public URL testers such as %s.", host))
        if host:
            # Safaricom sends no cookie and no X-Odoo-Database header: Odoo can
            # only pick this database if the host name leads to it alone.
            try:
                visible = db_list(force=True, host=host)
            except Exception:  # noqa: BLE001 - informational only
                visible = [self.env.cr.dbname]
            if visible != [self.env.cr.dbname]:
                problems.append(self.env._(
                    "Odoo resolves %(host)s to %(dbs)s, not to this database alone, so "
                    "M-PESA's callbacks would not reach %(db)s. Give the database its "
                    "own host name (dbfilter ^%%d$ or ^%%h$), or have the reverse "
                    "proxy set X-Odoo-Database: %(db)s on /pay/ke/.",
                    host=host, dbs=", ".join(visible) or "-", db=self.env.cr.dbname))
        return problems

    @api.model
    def _from_token(self, token):
        if not token or not re.fullmatch(r"[0-9a-f]{32}", token):
            return self.browse()
        return self.sudo().with_context(active_test=False).search(
            [("callback_token", "=", token)], limit=1)

    def _receiving_shortcode(self, business_shortcode):
        """The shortcode a payment reported as ``business_shortcode`` belongs to.

        ``self`` is the registration the callback came through (its token).
        A till registered on its own is that till. A store registration can
        carry its tills' confirmations: when the body names one of the store's
        tills, the payment goes there; when it names only the store, it stays
        on the store, and the cashier's pick decides later.
        """
        self.ensure_one()
        number = str(business_shortcode or "").strip()
        if not number or number in (self.number, self.c2b_shortcode):
            return self
        family = self.child_ids | self.parent_id | self.parent_id.child_ids
        match = family.filtered(lambda s: s.number == number and s.active)
        if match:
            return match[0]
        same_company = self.search(
            [("number", "=", number), ("company_id", "child_of", self.company_id.root_id.id)],
            limit=1)
        return same_company or self

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_regenerate_token(self):
        for rec in self:
            rec.sudo().callback_token = _new_token()
            rec.c2b_registered_at = False
            rec.message_post(body=self.env._(
                "New callback token. Register the C2B URLs again, and update any "
                "URL Safaricom holds for this shortcode."))

    def action_register_c2b(self):
        """Register this shortcode's confirmation and validation URLs with Daraja."""
        for rec in self:
            sudo = rec.sudo()
            confirmation = sudo._callback_url("c2b_confirm")
            validation = sudo._callback_url("c2b_validate")
            problems = rec._url_problems(confirmation)
            if problems and rec.state == "live":
                raise UserError("\n".join(problems))
            try:
                answer = sudo._client().c2b_register(
                    shortcode=rec.c2b_shortcode or rec.number,
                    confirmation_url=confirmation,
                    validation_url=validation,
                    response_type=rec.c2b_response_type,
                )
            except daraja.DarajaError as exc:
                if re.search(r"already registered", exc.message or "", re.I):
                    raise UserError(self.env._(
                        "Safaricom already holds C2B URLs for %(code)s. Production "
                        "registration is one-time: delete them on the Daraja portal "
                        "(Self Services > URL Management) or ask "
                        "apisupport@safaricom.co.ke, then register again.",
                        code=rec.c2b_shortcode or rec.number)) from exc
                raise UserError(self.env._("Daraja refused the registration: %s", exc)) from exc
            except daraja.DarajaTransportError as exc:
                raise UserError(self.env._("Daraja could not be reached: %s", exc)) from exc
            rec.c2b_registered_at = fields.Datetime.now()
            rec.message_post(body=self.env._(
                "C2B URLs registered for %(code)s (%(answer)s).",
                code=rec.c2b_shortcode or rec.number,
                answer=answer.get("ResponseDescription") or "OK"))
        return True

    def action_setup_accounting(self):
        """Create the shortcode's bank journal and its two accounts."""
        for rec in self.filtered(lambda s: not s.journal_id):
            rec.journal_id = rec._create_journal()
        return True

    def _create_journal(self):
        self.ensure_one()
        company = self.company_id
        Account = self.env["account.account"].sudo().with_company(company)
        label = self.display_name
        code_base = re.sub(r"\D", "", self.number)[-6:]
        bank_account = Account.create({
            "name": f"M-Pesa {label}",
            "code": self._free_account_code(company, "1104", code_base),
            "account_type": "asset_cash",
        })
        outstanding = Account.create({
            "name": f"M-Pesa {label} outstanding receipts",
            "code": self._free_account_code(company, "1105", code_base),
            "account_type": "asset_current",
            "reconcile": True,
        })
        suspense = company._mpesa_suspense_account()
        journal = self.env["account.journal"].sudo().create({
            "name": f"M-Pesa {label}"[:64],
            "type": "bank",
            "code": self._free_journal_code(company),
            "company_id": company.id,
            "default_account_id": bank_account.id,
            "suspense_account_id": suspense.id,
            "bank_statements_source": "undefined",
        })
        for line in journal.inbound_payment_method_line_ids:
            line.payment_account_id = outstanding
        self.message_post(body=self.env._(
            "Journal %(journal)s created, with bank account %(bank)s and "
            "outstanding receipts account %(out)s; suspense: %(suspense)s.",
            journal=journal.display_name, bank=bank_account.display_name,
            out=outstanding.display_name, suspense=suspense.display_name))
        return journal

    @api.model
    def _free_account_code(self, company, prefix, suffix):
        Account = self.env["account.account"].sudo().with_company(company)
        for code in [f"{prefix}{suffix}"] + [f"{prefix}{suffix}{i}" for i in range(1, 100)]:
            if not Account.search_count([("code", "=", code), ("company_ids", "in", company.id)]):
                return code
        raise UserError(self.env._("No free account code under %s.", prefix))

    @api.model
    def _free_journal_code(self, company):
        Journal = self.env["account.journal"].sudo()
        for i in range(1, 1000):
            code = f"MP{i:02d}" if i < 100 else f"M{i}"
            if not Journal.search_count([("code", "=", code), ("company_id", "=", company.id)]):
                return code
        raise UserError(self.env._("No free journal code."))

    def action_open_transactions(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "x_ke_mpesa.action_mpesa_transaction")
        action["domain"] = [("shortcode_id", "child_of", self.id)]
        action["context"] = {}
        return action
