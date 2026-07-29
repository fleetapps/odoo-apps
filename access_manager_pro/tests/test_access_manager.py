# -*- coding: utf-8 -*-
"""Integration tests for the Access Manager Pro engine.

They exercise the real extension points against ``res.partner`` (always present,
has a chatter, a phone field and an ``active`` field), acting as a plain
internal user so the restrictions actually apply.

Run with::

    odoo -i access_manager_pro --test-enable --stop-after-init
"""

from datetime import datetime, timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestAccessManager(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env["res.partner"]
        cls.Profile = cls.env["access.manager.profile"]
        cls.user = new_test_user(
            cls.env, login="am_user", groups="base.group_user")
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        cls.phone_field = cls.env["ir.model.fields"]._get("res.partner", "phone")

    # -- helpers ---------------------------------------------------------- #
    def _arch(self, view_type="form"):
        res = self.Partner.with_user(self.user).get_view(view_type=view_type)
        return etree.fromstring(res["arch"])

    def _profile(self, **vals):
        vals.setdefault("name", "Test Profile")
        vals.setdefault("user_ids", [(6, 0, self.user.ids)])
        return self.Profile.create(vals)

    # -- view mutation ---------------------------------------------------- #
    def test_field_invisible(self):
        self._profile(field_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "field_id": self.phone_field.id,
            "mode": "invisible",
        })])
        nodes = self._arch("form").xpath("//field[@name='phone']")
        self.assertTrue(nodes, "phone field should exist in the partner form")
        self.assertEqual(nodes[0].get("invisible"), "1")

    def test_field_invisible_list_column(self):
        self._profile(field_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "field_id": self.phone_field.id,
            "mode": "invisible",
        })])
        for node in self._arch("list").xpath("//field[@name='phone']"):
            self.assertEqual(node.get("column_invisible"), "1")

    def test_field_required_with_condition(self):
        self._profile(field_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "field_id": self.phone_field.id,
            "mode": "required",
            "condition": "type == 'invoice'",
        })])
        node = self._arch("form").xpath("//field[@name='phone']")[0]
        self.assertEqual(node.get("required"), "type == 'invoice'")

    def test_model_switches(self):
        self._profile(model_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "hide_create": True,
            "hide_delete": True,
        })])
        root = self._arch("form")
        self.assertEqual(root.get("create"), "0")
        self.assertEqual(root.get("delete"), "0")

    def test_admin_is_never_restricted(self):
        self._profile(
            user_ids=[(6, 0, self.env.ref("base.user_admin").ids)],
            field_rule_ids=[(0, 0, {
                "model_id": self.partner_model.id,
                "field_id": self.phone_field.id,
                "mode": "invisible",
            })])
        res = self.Partner.with_user(self.env.ref("base.user_admin")).get_view(
            view_type="form")
        node = etree.fromstring(res["arch"]).xpath("//field[@name='phone']")
        self.assertTrue(all(n.get("invisible") in (None, "0") for n in node),
                        "administrators must never be restricted")

    # -- read-only user --------------------------------------------------- #
    def test_readonly_user_blocks_write(self):
        self._profile(is_readonly_user=True)
        partner = self.Partner.create({"name": "RO target"})
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"comment": "nope"})

    def test_readonly_user_blocks_create(self):
        self._profile(is_readonly_user=True)
        with self.assertRaises(AccessError):
            self.Partner.with_user(self.user).create({"name": "blocked"})

    def test_readonly_user_can_still_read(self):
        self._profile(is_readonly_user=True)
        partner = self.Partner.create({"name": "readable"})
        # Should not raise.
        self.assertEqual(partner.with_user(self.user).name, "readable")

    # -- archive ---------------------------------------------------------- #
    def test_hide_archive_blocks_toggle(self):
        self._profile(model_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "hide_archive": True,
        })])
        partner = self.Partner.create({"name": "keep active"})
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"active": False})

    # -- domain rules ----------------------------------------------------- #
    def test_domain_read_filtering(self):
        secret = self.Partner.create({"name": "AM SECRET"})
        visible = self.Partner.create({"name": "AM VISIBLE"})
        self._profile(domain_rule_ids=[(0, 0, {
            "name": "Hide secret",
            "model_id": self.partner_model.id,
            "domain": "[('name', '=', 'AM SECRET')]",
            "perm_read": True,
        })])
        found = self.Partner.with_user(self.user).search(
            [("id", "in", (secret + visible).ids)])
        self.assertIn(visible, found)
        self.assertNotIn(secret, found)

    def test_domain_hard_write_block(self):
        target = self.Partner.create({"name": "LOCKED", "ref": "lock-me"})
        self._profile(domain_rule_ids=[(0, 0, {
            "name": "No editing locked",
            "model_id": self.partner_model.id,
            "domain": "[('ref', '=', 'lock-me')]",
            "perm_read": False,
            "perm_write": True,
        })])
        with self.assertRaises(AccessError):
            target.with_user(self.user).write({"comment": "x"})

    def test_domain_soft_does_not_block_write(self):
        target = self.Partner.create({"name": "SOFT", "ref": "soft-me"})
        self._profile(domain_rule_ids=[(0, 0, {
            "name": "Soft hide",
            "model_id": self.partner_model.id,
            "domain": "[('ref', '=', 'soft-me')]",
            "perm_read": True,
            "perm_write": True,
            "is_soft": True,
        })])
        # Soft rule never raises, even though write is ticked.
        target.with_user(self.user).write({"comment": "allowed"})
        self.assertEqual(target.comment, "allowed")

    # -- menus ------------------------------------------------------------ #
    def test_menu_blacklist(self):
        menu = self.env["ir.ui.menu"].create({"name": "AM Temp Menu"})
        child = self.env["ir.ui.menu"].create(
            {"name": "AM Temp Child", "parent_id": menu.id})
        self._profile(hidden_menu_ids=[(6, 0, menu.ids)])
        blacklist = self.env["ir.ui.menu"].with_user(
            self.user)._load_menus_blacklist()
        self.assertIn(menu.id, blacklist)
        self.assertIn(child.id, blacklist, "sub-menus must be hidden too")

    # -- export ----------------------------------------------------------- #
    def test_export_blocked(self):
        self._profile(model_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "hide_export": True,
        })])
        partner = self.Partner.create({"name": "no export"})
        with self.assertRaises(AccessError):
            partner.with_user(self.user).export_data(["name"])

    def test_invisible_field_stripped_from_export(self):
        self._profile(field_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "field_id": self.phone_field.id,
            "mode": "invisible",
        })])
        partner = self.Partner.create({"name": "exp", "phone": "123"})
        result = partner.with_user(self.user).export_data(["name", "phone"])
        # 'phone' is dropped, so only the name column remains.
        self.assertEqual(len(result["datas"][0]), 1)

    # -- scheduling / expiration ----------------------------------------- #
    def test_expired_profile_not_applied(self):
        self._profile(
            date_end=fields.Datetime.now() - timedelta(days=1),
            field_rule_ids=[(0, 0, {
                "model_id": self.partner_model.id,
                "field_id": self.phone_field.id,
                "mode": "invisible",
            })])
        node = self._arch("form").xpath("//field[@name='phone']")[0]
        self.assertIn(node.get("invisible"), (None, "0"),
                      "an expired profile must not restrict anything")

    def test_cron_revokes_expired(self):
        profile = self._profile(date_end=fields.Datetime.now() - timedelta(hours=1))
        self.Profile._cron_revoke_expired()
        self.assertFalse(profile.active)

    def test_time_window(self):
        profile = self._profile(restriction_time_based=True,
                                time_start=8.0, time_end=17.0, tz="UTC")
        self.assertTrue(profile._within_time_window(profile, datetime(2026, 1, 1, 12, 0)))
        self.assertFalse(profile._within_time_window(profile, datetime(2026, 1, 1, 20, 0)))
        profile.write({"time_start": 22.0, "time_end": 6.0})  # spans midnight
        self.assertTrue(profile._within_time_window(profile, datetime(2026, 1, 1, 23, 30)))
        self.assertFalse(profile._within_time_window(profile, datetime(2026, 1, 1, 12, 0)))

    # -- masking --------------------------------------------------------- #
    def test_masking_web_read(self):
        self._profile(field_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "field_id": self.phone_field.id,
            "mode": "masked",
            "mask_show_last": 2,
        })])
        partner = self.Partner.create({"name": "M", "phone": "5551234"})
        result = partner.with_user(self.user).web_read({"phone": {}})
        masked = result[0]["phone"]
        self.assertTrue(masked.startswith("•"))
        self.assertTrue(masked.endswith("34"))
        # The stored value is untouched (only the client read is masked).
        self.assertEqual(partner.phone, "5551234")

    # -- hide view types ------------------------------------------------- #
    def test_view_mode_stripped(self):
        self._profile(model_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "hide_pivot": True,
        })])
        action = self.env["ir.actions.act_window"].create({
            "name": "Partners", "res_model": "res.partner",
            "view_mode": "list,form,pivot",
        })
        view_mode = action.with_user(self.user).read(["view_mode"])[0]["view_mode"]
        self.assertNotIn("pivot", view_mode)
        self.assertIn("list", view_mode)

    # -- hierarchy ------------------------------------------------------- #
    def test_hierarchy_user_ids_own(self):
        allowed = self.Profile._hierarchy_user_ids(self.user, "own")
        self.assertEqual(set(allowed), {self.user.id})

    # -- JSON round trip ------------------------------------------------- #
    def test_json_round_trip(self):
        profile = self._profile(
            model_rule_ids=[(0, 0, {
                "model_id": self.partner_model.id, "hide_create": True})],
            field_rule_ids=[(0, 0, {
                "model_id": self.partner_model.id,
                "field_id": self.phone_field.id, "mode": "invisible"})])
        bundle = profile._serialize()
        created = self.Profile._import_bundle(bundle)
        self.assertEqual(len(created), 1)
        self.assertNotEqual(created.id, profile.id)
        self.assertTrue(created.model_rule_ids.hide_create)
        self.assertEqual(created.field_rule_ids.field_name, "phone")
        self.assertEqual(created.user_ids, self.user)

    # -- dashboard ------------------------------------------------------- #
    def test_dashboard_payload(self):
        self._profile(is_readonly_user=True)
        data = self.Profile.get_dashboard_data()
        for key in ("kpis", "restriction_split", "created_series",
                    "restricted_models", "insights", "heatmap", "users",
                    "restriction_load", "top_users", "by_company",
                    "expirations", "rule_mix"):
            self.assertIn(key, data)
        self.assertEqual(len(data["created_series"]), 6)
        self.assertEqual(len(data["restriction_load"]), 4)   # 4 load bands
        self.assertEqual(len(data["expirations"]), 4)        # 4 urgency buckets
        self.assertTrue(any(m["label"] == "Fields" for m in data["rule_mix"]))
        self.assertIn("config_score", data["insights"])

    def test_user_inspection(self):
        self._profile(field_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "field_id": self.phone_field.id, "mode": "invisible"})])
        result = self.Profile.get_user_inspection(self.user.id)
        self.assertEqual(result["user"]["id"], self.user.id)
        self.assertGreaterEqual(result["counts"]["rules"], 1)
        self.assertGreaterEqual(result["counts"]["fields"], 1)
