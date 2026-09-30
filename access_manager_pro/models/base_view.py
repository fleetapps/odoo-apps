# -*- coding: utf-8 -*-
"""View post-processing engine.

Every model inherits ``base``, so overriding :meth:`get_view` here lets Access
Manager Pro rewrite the resolved arch of *any* view of *any* model right before
it reaches the web client - the standard, framework-sanctioned extension point
(see the View Records reference).

Design notes
------------
* **Fast path first.** The very first thing we do is read the cached, compiled
  configuration.  Unrestricted users (the overwhelming majority, plus every
  superuser and app administrator) return immediately with the untouched arch.
* **Own-model scoping.** Form arches embed the sub-views of their o2m/m2m
  fields; those inner ``<field>`` nodes belong to the *comodel*, not to
  ``self``.  Every selector is therefore constrained with ``not(ancestor::
  field)`` so a rule on model A never accidentally rewrites a nested view of
  model B.
* **Never break a view.** Each individual mutation is defensive: a bad admin
  XPath or an unexpected arch shape is logged and skipped, never raised.
* **Version aware.** List roots are ``<list>`` on 18/19 and ``<tree>`` on 17;
  chatter is a ``<chatter/>`` tag but legacy/custom forms may still use a
  ``<div class="oe_chatter">``.  Both shapes are handled.

References
----------
* View records .... https://www.odoo.com/documentation/19.0/developer/reference/user_interface/view_records.html
* View arch ....... https://www.odoo.com/documentation/19.0/developer/reference/user_interface/view_architectures.html
"""

import ast
import json
import logging

from lxml import etree

from odoo import api, models
from .access_profile import SKIP_KEY

_logger = logging.getLogger(__name__)

# Attribute set on a node to force-hide / force-readonly it unconditionally.
_TRUE = "1"


class Base(models.AbstractModel):
    _inherit = "base"

    # ------------------------------------------------------------------ #
    #  Entry point
    # ------------------------------------------------------------------ #
    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        res = super().get_view(view_id=view_id, view_type=view_type, **options)
        if self.env.su or self.env.context.get(SKIP_KEY):
            return res
        config = self.env["access.manager.profile"]._get_access_config()
        if not config["restricted"]:
            return res

        model_cfg = config["models"].get(self._name)
        globals_ = config["globals"]
        # Nothing targets this model and no global switch is active -> untouched.
        if not model_cfg and not any(globals_.values()):
            return res

        try:
            arch = etree.fromstring(res["arch"])
        except etree.XMLSyntaxError:
            return res

        root_tag = arch.tag
        is_list = root_tag in ("list", "tree")
        is_form = root_tag == "form"

        if model_cfg:
            self._am_apply_field_rules(arch, model_cfg["fields"], is_list)
            self._am_apply_element_rules(arch, model_cfg["elements"])
            self._am_apply_switches(arch, model_cfg["switches"], is_list, is_form)
        self._am_apply_globals(arch, globals_, is_list, is_form)

        res["arch"] = etree.tostring(arch, encoding="unicode")
        return res

    # ------------------------------------------------------------------ #
    #  Field rules
    # ------------------------------------------------------------------ #
    def _am_apply_field_rules(self, arch, field_rules, is_list):
        for rule in field_rules:
            name = rule["field"]
            xpath = f"//field[@name='{name}'][not(ancestor::field)]"
            try:
                nodes = arch.xpath(xpath)
            except etree.XPathError:
                continue
            for node in nodes:
                self._am_apply_one_field_rule(node, rule, is_list)

    def _am_apply_one_field_rule(self, node, rule, is_list):
        mode, condition = rule["mode"], rule["condition"]
        if mode == "invisible":
            if is_list and not condition:
                node.set("column_invisible", _TRUE)
            self._am_set_condition(node, "invisible", condition)
        elif mode in ("readonly", "masked"):
            # A masked field is shown read-only; its value is masked at read time.
            self._am_set_condition(node, "readonly", condition)
        elif mode == "required":
            self._am_set_condition(node, "required", condition)
            # A required field cannot also be hidden by us.
            node.attrib.pop("column_invisible", None)

        if rule["no_create"] or rule["no_open"]:
            self._am_update_field_options(
                node, no_create=rule["no_create"], no_open=rule["no_open"])

        if rule.get("dropdown_domain"):
            self._am_merge_field_domain(node, rule["dropdown_domain"])

    @staticmethod
    def _am_merge_field_domain(node, extra):
        """AND ``extra`` into the ``domain`` attribute of a relational field.

        The attribute is a Python *expression* evaluated in the browser against
        the record, not a literal, so the two domains are combined the same way
        the expression language allows: ``["&"] + (a) + (b)``. The client's
        evaluator (``@web/core/py_js``) implements ``+`` on lists, so this
        stays a single valid domain however dynamic either side is.

        A pre-existing domain that is not list-shaped (a dict, as reference
        fields may carry) is left alone rather than corrupted.
        """
        extra = extra.strip()
        existing = (node.get("domain") or "").strip()
        if not existing or existing in ("[]", "()"):
            node.set("domain", extra)
            return
        if not existing.startswith(("[", "(")):
            _logger.debug(
                "Access Manager: field %r keeps its non-list domain %r; the "
                "configured field domain was not merged", node.get("name"),
                existing)
            return
        node.set("domain", '["&"] + (%s) + (%s)' % (existing, extra))

    @staticmethod
    def _am_set_condition(node, attr, condition):
        """Set ``attr`` to ``1`` (unconditional) or OR-in a condition."""
        value = condition or _TRUE
        existing = node.get(attr)
        if existing and existing not in ("0", _TRUE):
            # Preserve an existing conditional expression by combining them.
            node.set(attr, f"({existing}) or ({value})")
        else:
            node.set(attr, value)

    @staticmethod
    def _am_update_field_options(node, no_create=False, no_open=False):
        """Merge quick-create / open-link flags into the field ``options`` dict.

        Existing options may be written Python-style (``True``) or JSON-style
        (``true``); try both so we never clobber a legitimate widget option.
        """
        raw = node.get("options") or "{}"
        opts = None
        for parser in (ast.literal_eval, json.loads):
            try:
                candidate = parser(raw)
            except (ValueError, SyntaxError, TypeError):
                continue
            if isinstance(candidate, dict):
                opts = candidate
                break
        if opts is None:
            opts = {}
        if no_create:
            opts["no_create"] = True
            opts["no_create_edit"] = True
        if no_open:
            opts["no_open"] = True
        node.set("options", repr(opts))

    # ------------------------------------------------------------------ #
    #  View element rules
    # ------------------------------------------------------------------ #
    def _am_apply_element_rules(self, arch, element_rules):
        for rule in element_rules:
            xpath = self._am_element_xpath(rule["type"], rule["selector"])
            if not xpath:
                continue
            try:
                nodes = arch.xpath(xpath)
            except etree.XPathError:
                _logger.debug("Access Manager: bad element selector %r", rule["selector"])
                continue
            attr = "invisible" if rule["mode"] == "invisible" else "readonly"
            for node in nodes:
                # Buttons have no meaningful "readonly"; hide them instead.
                target_attr = "invisible" if node.tag == "button" else attr
                self._am_set_condition(node, target_attr, rule["condition"])

    @staticmethod
    def _am_element_xpath(element_type, selector):
        selector = (selector or "").strip()
        if not selector:
            return None
        scope = "[not(ancestor::field)]"
        if element_type == "button":
            return f"//button[@name='{selector}']{scope}"
        if element_type == "page":
            return (f"//page[@string='{selector}']{scope}"
                    f" | //page[@name='{selector}']{scope}")
        if element_type in ("filter", "groupby"):
            return f"//filter[@name='{selector}']"
        if element_type == "kanban":
            return (f"//*[@name='{selector}']{scope}"
                    f" | //a[contains(@class,'{selector}')]"
                    f" | //button[contains(@class,'{selector}')]")
        if element_type == "xpath":
            return selector
        return None

    # ------------------------------------------------------------------ #
    #  Model-level switches
    # ------------------------------------------------------------------ #
    def _am_apply_switches(self, arch, switches, is_list, is_form):
        if switches.get("readonly"):
            # A read-only model can neither be created, edited nor deleted in UI.
            for attr in ("create", "edit", "delete"):
                arch.set(attr, "0")
        if switches.get("hide_create"):
            arch.set("create", "0")
        if switches.get("hide_edit"):
            arch.set("edit", "0")
        if switches.get("hide_delete"):
            arch.set("delete", "0")
        if switches.get("hide_duplicate") and is_form:
            arch.set("duplicate", "0")
        if is_list and switches.get("hide_export"):
            arch.set("export_xlsx", "0")
        if is_list and switches.get("hide_import"):
            arch.set("import", "0")
        if is_form:
            self._am_apply_chatter(arch, switches)

    # Fine-grained chatter switch -> CSS class added to the <chatter/> node.
    #
    # This works because of ``ViewCompiler.compileNode``: after a node is handed
    # to its compiler it runs ``copyAttributes(node, compiledNode)``, which
    # copies the arch node's ``class`` onto the compiled element
    # (web/static/src/views/view_compiler.js). ``compileChatter`` itself never
    # reads the attribute, but its result - the ``o-mail-Form-chatter`` hook -
    # receives the class from that generic step, and mail's later
    # ``cloneNode(true)`` for the sheet-background layout carries it along. The
    # matching rules live in static/src/scss/access_manager.scss; because the
    # class lands on this form's chatter only, the hiding is naturally scoped to
    # the model - no component patching (and no fragile cross-version import)
    # required. Whole-chatter removal is done structurally below.
    #
    # Hiding a button is not a restriction on its own, so posting and activity
    # scheduling are additionally refused at the model - see models/mail_thread.py.
    _CHATTER_CLASSES = {
        "hide_send_message": "o_am_hide_message",
        "hide_log_note": "o_am_hide_note",
        "hide_activity": "o_am_hide_activity",
        "hide_followers": "o_am_hide_followers",
    }

    def _am_apply_chatter(self, arch, flags):
        if flags.get("hide_chatter"):
            for node in arch.xpath("//chatter"):
                node.getparent().remove(node)
            for node in arch.xpath("//div[contains(@class, 'oe_chatter')]"):
                node.getparent().remove(node)
            return
        classes = [css for flag, css in self._CHATTER_CLASSES.items()
                   if flags.get(flag)]
        if not classes:
            return
        for node in arch.xpath("//chatter"):
            merged = " ".join(filter(None, [node.get("class", "")] + classes))
            node.set("class", merged)

    # ------------------------------------------------------------------ #
    #  Global (all-model) switches
    # ------------------------------------------------------------------ #
    def _am_apply_globals(self, arch, globals_, is_list, is_form):
        if is_list:
            if globals_.get("hide_export"):
                arch.set("export_xlsx", "0")
            if globals_.get("hide_import"):
                arch.set("import", "0")
        if globals_.get("hide_search_panel"):
            for node in arch.xpath("//searchpanel"):
                node.getparent().remove(node)
        if is_form:
            self._am_apply_chatter(arch, {
                "hide_chatter": globals_.get("hide_chatter"),
                "hide_send_message": globals_.get("hide_send_message"),
                "hide_log_note": globals_.get("hide_log_note"),
                "hide_activity": globals_.get("hide_activity"),
                "hide_followers": globals_.get("hide_followers"),
            })
