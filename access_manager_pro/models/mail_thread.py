# -*- coding: utf-8 -*-
"""Server-side enforcement of the chatter restrictions.

Why this file exists
--------------------
The chatter switches used to be UI-only.  The class Access Manager puts on the
``<chatter/>`` node *does* reach the browser - ``ViewCompiler.compileNode``
copies a node's ``class`` onto its compiled counterpart before handing it back
(``web/static/src/views/view_compiler.js``, ``copyAttributes``), which is how
the ``o-mail-Form-chatter`` hook ends up carrying it - so the buttons really do
disappear.  But a hidden button is only a hidden button: ``message_post`` and
``mail.activity.create`` are ordinary public methods, so anyone who can reach
the ORM could still email a customer from a record or schedule an activity on a
model they were restricted from.

So the two chatter actions that push data *out of* Odoo - posting a message and
scheduling an activity - are enforced here, at the model, exactly like export
and the record domains are.  The switches are read from the same compiled
configuration, so a global switch and a per-model one behave identically.

Deliberate exemptions
---------------------
* ``message_type='notification'`` is never blocked. Tracking messages, workflow
  notifications and ``_message_log`` all post as notifications on the user's
  behalf; refusing those would make a restricted user unable to save a record.
  Only ``comment`` - what the composer sends - is subject to the switches.
* Discuss channels are never blocked. "Hide Send message" is about writing to a
  contact from a business record; it is not a Discuss ban.
* ``message_subscribe`` is intentionally *not* overridden. Odoo calls it
  internally on almost every create and on many writes (auto-subscribe), so
  refusing it would break ordinary record creation rather than the Followers
  button. Hiding followers therefore stays a UI restriction, which is what the
  field help says.

Reference: https://www.odoo.com/documentation/19.0/developer/reference/backend/mixins.html
"""

from odoo import _, api, models
from odoo.exceptions import AccessError

from .access_profile import SKIP_KEY

# Threads that are conversations in their own right rather than a record's
# chatter; the chatter switches do not apply to them.
_CHATTER_EXEMPT_MODELS = frozenset({"discuss.channel", "mail.channel"})


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    # ------------------------------------------------------------------ #
    #  Shared switch lookup
    # ------------------------------------------------------------------ #
    @api.model
    def _am_chatter_flags(self, model_name=None):
        """Effective chatter switches for ``model_name`` (defaults to self).

        A global switch and a per-model switch mean the same thing, so they are
        OR-ed together here rather than at every call site.
        """
        model_name = model_name or self._name
        config = self.env["access.manager.profile"]._get_access_config()
        if not config["restricted"] or model_name in _CHATTER_EXEMPT_MODELS:
            return {}
        globals_ = config["globals"]
        switches = config["models"].get(model_name, {}).get("switches", {})
        flags = {
            key: bool(globals_.get(key) or switches.get(key))
            for key in ("hide_chatter", "hide_send_message", "hide_log_note",
                        "hide_activity", "hide_followers")
        }
        # Removing the whole chatter removes everything inside it.
        if flags["hide_chatter"]:
            flags = dict.fromkeys(flags, True)
        return flags

    def _am_chatter_skip(self):
        return self.env.su or self.env.context.get(SKIP_KEY)

    # ------------------------------------------------------------------ #
    #  Posting
    # ------------------------------------------------------------------ #
    def message_post(self, **kwargs):
        # ``message_post`` is keyword-only on 17-19, so forwarding **kwargs is
        # both complete and version-proof.
        if not self._am_chatter_skip():
            self._am_assert_post_allowed(kwargs)
        return super().message_post(**kwargs)

    def _am_assert_post_allowed(self, kwargs):
        if kwargs.get("message_type", "notification") != "comment":
            return
        flags = self._am_chatter_flags()
        if not flags:
            return
        is_note = self._am_post_is_note(kwargs)
        if is_note and flags["hide_log_note"]:
            raise AccessError(_(
                "Logging notes is disabled for you on %s.",
                self._description or self._name))
        if not is_note and flags["hide_send_message"]:
            raise AccessError(_(
                "Sending messages is disabled for you on %s.",
                self._description or self._name))

    def _am_post_is_note(self, kwargs):
        """Whether a ``message_post`` call is a *Log note* rather than a message.

        Mirrors ``mail.thread.message_post``'s own resolution order:
        ``subtype_xmlid`` wins, then ``subtype_id``, and an unspecified subtype
        defaults to ``mail.mt_note``.
        """
        xmlid = kwargs.get("subtype_xmlid")
        if xmlid:
            return xmlid == "mail.mt_note"
        subtype_id = kwargs.get("subtype_id")
        if not subtype_id:
            return True
        note_id = self.env["ir.model.data"]._xmlid_to_res_id(
            "mail.mt_note", raise_if_not_found=False)
        return subtype_id == note_id


class MailActivity(models.Model):
    _inherit = "mail.activity"

    @api.model_create_multi
    def create(self, vals_list):
        if not (self.env.su or self.env.context.get(SKIP_KEY)):
            self._am_assert_activity_allowed(vals_list)
        return super().create(vals_list)

    @api.model
    def _am_assert_activity_allowed(self, vals_list):
        thread = self.env["mail.thread"]
        checked = {}
        for vals in vals_list:
            model_name = vals.get("res_model")
            if not model_name and vals.get("res_model_id"):
                model_name = self.env["ir.model"].sudo().browse(
                    vals["res_model_id"]).model
            if not model_name:
                continue
            if model_name not in checked:
                checked[model_name] = thread._am_chatter_flags(model_name)
            flags = checked[model_name]
            if flags and flags["hide_activity"]:
                raise AccessError(_(
                    "Scheduling activities is disabled for you on %s.",
                    self.env["ir.model"].sudo()._get(model_name).name
                    or model_name))
