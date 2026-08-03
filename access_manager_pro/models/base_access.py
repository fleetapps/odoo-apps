# -*- coding: utf-8 -*-
"""Server-side access enforcement.

Hiding things in a view is a usability layer, not security - a determined user
can still reach the ORM over XML-RPC or the shell.  This module inherits
``base`` a second time to add the enforcement that *does* hold at the server:

* **Read-only users** cannot ``create`` / ``write`` / ``unlink`` any business
  record (a small technical whitelist keeps sessions working).
* **Record (domain) rules** hide matching records from every search and raise
  on create/edit/delete of a matching record (unless the rule is *soft*).
* **Export** is blocked model-wide when configured, and unconditionally
  invisible fields are stripped from any export payload.

All checks share the same cheap gate: read the cached configuration, and for
anything other than a restricted user return immediately.

Reference: https://www.odoo.com/documentation/19.0/developer/reference/backend/security.html
"""

import logging
import time
from datetime import date, datetime

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.safe_eval import safe_eval

from .access_profile import SKIP_KEY

try:  # Odoo 19 exposes the domain AST; 17/18 only ever pass list domains.
    from odoo.fields import Domain
except ImportError:  # pragma: no cover - Odoo < 19
    Domain = None

_logger = logging.getLogger(__name__)

# Technical models a read-only user must still be able to write to, otherwise
# the session itself breaks (presence heartbeat, per-user settings, attachment
# streaming for report/preview, change tracking).  Transient models (wizards)
# are always allowed - they are ephemeral, and the *business* write they may
# trigger still funnels back through ``write``/``create`` and is caught there.
READONLY_WHITELIST = frozenset({
    "bus.presence",
    "bus.bus",
    "res.users.log",
    "res.users.settings",
    "res.device",
    "res.device.log",
    "ir.attachment",
    "mail.tracking.value",
    "web_tour.tour",
})


class Base(models.AbstractModel):
    _inherit = "base"

    # ------------------------------------------------------------------ #
    #  Shared helpers
    # ------------------------------------------------------------------ #
    def _am_skip(self):
        return self.env.su or self.env.context.get(SKIP_KEY)

    def _am_config(self):
        return self.env["access.manager.profile"]._get_access_config()

    # ------------------------------------------------------------------ #
    #  Write barriers
    # ------------------------------------------------------------------ #
    @api.model_create_multi
    def create(self, vals_list):
        if not self._am_skip():
            self._am_assert_writable("create")
        records = super().create(vals_list)
        if records and not self._am_skip():
            records._am_assert_domain_allowed("create")
        return records

    def write(self, vals):
        if not self._am_skip():
            self._am_assert_writable("write")
            self._am_assert_domain_allowed("write")
            if "active" in vals:
                self._am_assert_archive_allowed()
        return super().write(vals)

    def _am_assert_archive_allowed(self):
        """Block archive/unarchive (which toggles ``active``) when configured.

        Enforcing on the write of ``active`` covers every path - the Archive
        action, ``toggle_active`` and a direct field edit alike - so the
        restriction holds even though the menu entry is only hidden best-effort
        in the client.
        """
        switches = self._am_config()["models"].get(self._name, {}).get("switches", {})
        if switches.get("hide_archive"):
            raise AccessError(_(
                "Archiving and unarchiving is disabled for you on %s.",
                self._description or self._name))

    def unlink(self):
        if not self._am_skip():
            self._am_assert_writable("unlink")
            self._am_assert_domain_allowed("unlink")
        return super().unlink()

    def _am_assert_writable(self, operation):
        """Block write operations for a read-only user."""
        if not self._am_config()["is_readonly_user"]:
            return
        if self._transient or self._name in READONLY_WHITELIST:
            return
        # A read-only user may still maintain their own user record/preferences.
        if self._name == "res.users" and self.ids and set(self.ids) <= {self.env.uid}:
            return
        raise AccessError(_(
            "Your access is read-only. You are not allowed to %(op)s "
            "%(model)s records.",
            op=operation, model=self._description or self._name))

    # ------------------------------------------------------------------ #
    #  Record (domain) rules - hard enforcement
    # ------------------------------------------------------------------ #
    def _am_assert_domain_allowed(self, operation):
        config = self._am_config()
        if self._name not in config["domain_models"]:
            return
        eval_ctx = self._am_domain_eval_context()
        for rule in config["domains"].get(self._name, ()):
            if rule["soft"] or not rule.get(operation):
                continue
            domain = self._am_eval_domain(rule["domain"], eval_ctx)
            if not domain:
                continue
            matching = self.sudo().filtered_domain(domain)
            if matching:
                raise AccessError(_(
                    "You are not allowed to %(op)s '%(record)s' "
                    "(restriction: %(rule)s).",
                    op=operation,
                    record=matching[:1].display_name,
                    rule=rule["name"]))

    # ------------------------------------------------------------------ #
    #  Record (domain) rules - read filtering
    # ------------------------------------------------------------------ #
    def _search(self, domain, *args, **kwargs):
        if not self._am_skip():
            domain = self._am_augment_search_domain(domain)
        return super()._search(domain, *args, **kwargs)

    def _am_augment_search_domain(self, domain):
        config = self._am_config()
        if self._name not in config["domain_models"]:
            return domain
        eval_ctx = self._am_domain_eval_context()
        restrictions = []
        for rule in config["domains"].get(self._name, ()):
            if not rule["read"]:
                continue
            rule_domain = self._am_eval_domain(rule["domain"], eval_ctx)
            if rule_domain:
                restrictions.append(rule_domain)
        if not restrictions:
            return domain

        # Hide the matching records: AND NOT(rule_domain) for every rule.
        #
        # Odoo 19 hands ``_search`` a ``Domain`` object on a number of internal
        # paths (relational field domains, ``any`` sub-queries, group
        # expansion); only public callers still pass a list. Combine in
        # whichever form we were handed, so the restriction holds on every path
        # instead of quietly dropping out on the ones that are not lists.
        if Domain is not None and isinstance(domain, Domain):
            for rule_domain in restrictions:
                domain &= ~Domain(rule_domain)
            return domain
        if not isinstance(domain, (list, tuple)):
            return domain
        exclusions = [["!"] + self._am_normalize_domain(d) for d in restrictions]
        return self._am_and_domains([list(domain)] + exclusions)

    # ------------------------------------------------------------------ #
    #  Sensitive-data masking (client read paths only)
    # ------------------------------------------------------------------ #
    # Masking is applied on ``web_read`` - the RPC the web client uses for form
    # and list values - never on the raw ``read``, so server-side business logic
    # keeps seeing the real values and only the UI is masked. ``web_search_read``
    # delegates to ``web_read`` internally, so overriding ``web_read`` alone
    # covers both (and avoids double-masking).
    def web_read(self, *args, **kwargs):
        result = super().web_read(*args, **kwargs)
        if not self._am_skip() and self._name in self._am_config()["masked_models"]:
            self._am_mask_records(result)
        return result

    def _am_mask_fields(self):
        """{field_name: (mask_char, show_last)} for masked fields of this model."""
        model_cfg = self._am_config()["models"].get(self._name, {})
        return {
            rule["field"]: (rule["mask_char"], rule["mask_show_last"])
            for rule in model_cfg.get("fields", ())
            if rule["mode"] == "masked"
        }

    def _am_mask_records(self, records):
        if not records:
            return
        masks = self._am_mask_fields()
        if not masks:
            return
        for record in records:
            if not isinstance(record, dict):
                continue
            for field, (char, show_last) in masks.items():
                # Only mask plain text values; leave relational/numeric widgets
                # untouched so masking never corrupts their rendering.
                if isinstance(record.get(field), str) and record[field]:
                    record[field] = self._am_mask_value(record[field], char, show_last)

    @staticmethod
    def _am_mask_value(text, char, show_last):
        char = char or "•"
        if show_last and show_last < len(text):
            return char * (len(text) - show_last) + text[-show_last:]
        return char * max(len(text), 4)

    # ------------------------------------------------------------------ #
    #  Export enforcement
    # ------------------------------------------------------------------ #
    def export_data(self, fields_to_export):
        if not self._am_skip():
            config = self._am_config()
            model_cfg = config["models"].get(self._name, {})
            switches = model_cfg.get("switches", {})
            if switches.get("hide_export") or config["globals"].get("hide_export"):
                raise AccessError(_(
                    "Export is disabled for you on %s.",
                    self._description or self._name))
            blocked = {
                rule["field"] for rule in model_cfg.get("fields", ())
                if rule["mode"] == "invisible" and not rule["condition"]
            }
            if blocked:
                fields_to_export = [
                    fname for fname in fields_to_export
                    if fname.split("/")[0] not in blocked
                ]
        return super().export_data(fields_to_export)

    # ------------------------------------------------------------------ #
    #  Domain utilities
    # ------------------------------------------------------------------ #
    def _am_domain_eval_context(self):
        """Dynamic helpers available inside a domain rule (mirrors ir.rule)."""
        return {
            "user": self.env.user,
            "uid": self.env.uid,
            "company_id": self.env.company.id,
            "company_ids": self.env.companies.ids,
            "time": time,
            "datetime": datetime,
            "date": date,
            "relativedelta": relativedelta,
            "today": fields.Date.today(),
            "now": fields.Datetime.now(),
            "context_today": lambda: fields.Date.context_today(self),
            "context": dict(self.env.context),
        }

    def _am_eval_domain(self, domain_str, eval_ctx):
        """Evaluate a rule domain; return a non-empty list, else ``None``.

        An empty/invalid domain is treated as a no-op so a half-configured rule
        can never silently hide (or block) every record of a model.
        """
        try:
            domain = safe_eval(domain_str or "[]", eval_ctx)
        except Exception as exc:  # noqa: BLE001 - never break a query on bad config
            _logger.warning("Access Manager: invalid domain %r on %s: %s",
                            domain_str, self._name, exc)
            return None
        if isinstance(domain, (list, tuple)) and domain:
            return list(domain)
        return None

    @staticmethod
    def _am_normalize_domain(domain):
        """Return ``domain`` in explicit prefix (Polish) form.

        This is Odoo's own normalisation, inlined to stay independent of the
        ``odoo.osv.expression`` / ``odoo.fields.Domain`` module reshuffle across
        17 -> 19.  Normalising guarantees a single ``!`` in front negates the
        *whole* expression, not just its first leaf.
        """
        result = []
        expected = 1
        arity = {"!": 1, "&": 2, "|": 2}
        for token in domain:
            if expected == 0:
                result.insert(0, "&")
                expected = 1
            if isinstance(token, (list, tuple)):
                expected -= 1
                result.append(tuple(token))
            else:
                expected += arity.get(token, 0) - 1
                result.append(token)
        return result

    @classmethod
    def _am_and_domains(cls, domains):
        """AND several (possibly un-normalised) domains together."""
        normalized = [cls._am_normalize_domain(d) for d in domains if d]
        if not normalized:
            return []
        combined = normalized[0]
        for dom in normalized[1:]:
            combined = ["&"] + combined + dom
        return combined
