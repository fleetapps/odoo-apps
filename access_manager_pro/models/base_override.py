# -*- coding: utf-8 -*-
"""Arch post-processing for every model by inheriting the 'base' abstract model.

get_view() is the public entry point returning a view's resolved arch to the
web client (View records:
https://www.odoo.com/documentation/19.0/developer/reference/user_interface/view_records.html).
Odoo 19 notes: list views use the <list> root element; list-column hiding uses
column_invisible (view_architectures reference).
"""
from lxml import etree
from odoo import api, models
from odoo.exceptions import AccessError
from odoo.tools.translate import _


class Base(models.AbstractModel):
    _inherit = "base"

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        res = super().get_view(view_id=view_id, view_type=view_type, **options)
        if self.env.su or self.env.user.has_group(
                "access_manager_pro.group_access_manager_admin"):
            return res
        profiles = self.env["access.manager.profile"]._applicable_profiles(
            model_name=self._name)
        if not profiles:
            return res
        arch = etree.fromstring(res["arch"])
        root_is_list = arch.tag == "list"

        for p in profiles:
            for r in p.field_rule_ids.filtered(lambda r: r.model_name == self._name):
                for node in arch.xpath(f"//field[@name='{r.field_name}']"):
                    if r.mode == "invisible":
                        node.set("invisible", "1")
                        if root_is_list:
                            node.set("column_invisible", "1")
                    elif r.mode == "readonly":
                        node.set("readonly", "1")
                    else:
                        node.set("required", "1")
            for r in p.element_rule_ids.filtered(lambda r: r.model_name == self._name):
                if r.element_type == "button":
                    xp = f"//button[@name='{r.selector}']"
                elif r.element_type == "page":
                    xp = (f"//page[@string='{r.selector}']"
                          f" | //page[@name='{r.selector}']")
                else:
                    xp = r.selector
                try:
                    for node in arch.xpath(xp):
                        node.set("invisible" if r.mode == "invisible"
                                 else "readonly", "1")
                except etree.XPathError:
                    continue  # never break a view on a bad admin xpath
            for r in p.model_rule_ids.filtered(lambda r: r.model_name == self._name):
                if r.hide_create:
                    arch.set("create", "0")
                if r.hide_edit:
                    arch.set("edit", "0")
                if r.hide_delete:
                    arch.set("delete", "0")
                if r.hide_duplicate:
                    arch.set("duplicate", "0")
                if r.block_export:
                    arch.set("export_xlsx", "0")
                if r.hide_chatter and view_type == "form":
                    for node in arch.xpath("//chatter"):
                        node.getparent().remove(node)

        res["arch"] = etree.tostring(arch, encoding="unicode")
        return res

    def export_data(self, fields_to_export):
        """Server-side export enforcement (UI hiding alone is not security)."""
        if not self.env.su:
            for p in self.env["access.manager.profile"]._applicable_profiles(
                    model_name=self._name):
                for r in p.model_rule_ids:
                    if r.model_name == self._name and r.block_export:
                        raise AccessError(_("Export is disabled for you on %s.")
                                          % self._description)
        return super().export_data(fields_to_export)
