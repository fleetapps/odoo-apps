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
from odoo.exceptions import AccessDenied, AccessError, ValidationError
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

    # -- aggregates must not leak past a record rule ---------------------- #
    def test_domain_filtering_applies_to_read_group(self):
        """Pivot/graph totals go through ``_search``, so they are filtered too.

        This is the regression guard for the whole aggregate surface: if a
        future Odoo stops building ``_read_group``'s query from ``_search``,
        restricted records would silently reappear in every count and sum, and
        only this test would notice.
        """
        secret = self.Partner.create({"name": "AM AGG SECRET", "ref": "agg-x"})
        visible = self.Partner.create({"name": "AM AGG VISIBLE", "ref": "agg-y"})
        self._profile(domain_rule_ids=[(0, 0, {
            "name": "Hide agg secret",
            "model_id": self.partner_model.id,
            "domain": "[('ref', '=', 'agg-x')]",
            "perm_read": True,
        })])
        scope = [("id", "in", (secret + visible).ids)]
        as_user = self.Partner.with_user(self.user)

        groups = as_user._read_group(scope, [], ["__count"])
        self.assertEqual(groups[0][0], 1, "aggregates must exclude hidden records")
        self.assertEqual(as_user.search_count(scope), 1)

        by_ref = dict(as_user._read_group(scope, ["ref"], ["__count"]))
        self.assertNotIn("agg-x", by_ref, "a hidden record must not form a group")
        self.assertIn("agg-y", by_ref)

    # -- external API (RPC) blocking -------------------------------------- #
    def test_rpc_blocked_for_non_interactive_credentials(self):
        self._profile(disable_rpc=True)
        credential = {"login": self.user.login, "password": "am_user",
                      "type": "password"}
        with self.assertRaises(AccessDenied):
            self.user._check_credentials(credential, {"interactive": False})

    def test_rpc_block_leaves_the_web_session_alone(self):
        """Only non-interactive credentials are refused.

        Asserted on the discriminator rather than by running a real login, so
        the test does not depend on password hashing: ``_check_credentials``
        verifies ``env.user``'s password, not ``self``'s.
        """
        self._profile(disable_rpc=True)
        Users = self.env["res.users"]
        self.assertTrue(Users._am_is_interactive((None, {"interactive": True}), {}))
        self.assertFalse(Users._am_is_interactive((None, {"interactive": False}), {}))
        # An unknown caller is assumed interactive, exactly like Odoo does.
        self.assertTrue(Users._am_is_interactive((None,), {}))
        self.assertTrue(Users._am_is_interactive((None, None), {}))
        # The switch itself is on, and it does not also block plain login.
        self.assertTrue(self.Profile._rpc_disabled_for(self.user))
        self.assertFalse(self.Profile._login_disabled_for(self.user))

    def test_rpc_block_never_locks_out_an_administrator(self):
        admin = self.env.ref("base.user_admin")
        self._profile(user_ids=[(6, 0, admin.ids)], disable_rpc=True)
        self.assertFalse(
            self.Profile._rpc_disabled_for(admin),
            "settings administrators must never be cut off from the API")

    def test_login_block_survives_the_sudo_login_path(self):
        """``res.users._login`` calls ``_check_credentials`` under sudo.

        An ``env.su`` early return in the guard would disable the login block on
        the one path every real login takes, so assert the guard fires there.
        """
        self._profile(disable_login=True)
        credential = {"login": self.user.login, "password": "am_user",
                      "type": "password"}
        with self.assertRaises(AccessDenied):
            self.user.with_user(self.user).sudo()._check_credentials(
                credential, {"interactive": True})

    # -- field dropdown domain -------------------------------------------- #
    def test_dropdown_domain_applied(self):
        parent_field = self.env["ir.model.fields"]._get("res.partner", "parent_id")
        self._profile(field_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "field_id": parent_field.id,
            "mode": "readonly",
            "dropdown_domain": "[('is_company', '=', True)]",
        })])
        node = self._arch("form").xpath("//field[@name='parent_id']")[0]
        self.assertIn("[('is_company', '=', True)]", node.get("domain") or "")

    def test_dropdown_domain_merges_with_an_existing_one(self):
        node = etree.fromstring("<field name='x' domain=\"[('a','=',1)]\"/>")
        self.Partner._am_merge_field_domain(node, "[('b','=',2)]")
        self.assertEqual(node.get("domain"),
                         '["&"] + ([(\'a\',\'=\',1)]) + ([(\'b\',\'=\',2)])')

    def test_dropdown_domain_rejects_nonsense(self):
        with self.assertRaises(ValidationError):
            self._profile(field_rule_ids=[(0, 0, {
                "model_id": self.partner_model.id,
                "field_id": self.phone_field.id,
                "mode": "invisible",
                "dropdown_domain": "[('a', '=',",
            })])

    # -- chatter enforcement ---------------------------------------------- #
    def test_send_message_blocked_server_side(self):
        self._profile(model_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "hide_send_message": True,
        })])
        partner = self.Partner.create({"name": "no mail"})
        with self.assertRaises(AccessError):
            partner.with_user(self.user).message_post(
                body="hello", message_type="comment",
                subtype_xmlid="mail.mt_comment")

    def test_log_note_still_allowed_when_only_messages_are_blocked(self):
        self._profile(model_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "hide_send_message": True,
        })])
        partner = self.Partner.create({"name": "note ok"})
        message = partner.with_user(self.user).message_post(
            body="internal", message_type="comment",
            subtype_xmlid="mail.mt_note")
        self.assertTrue(message)

    def test_tracking_notifications_are_never_blocked(self):
        """A restricted user must still be able to save a tracked record."""
        self._profile(model_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "hide_chatter": True,
        })])
        partner = self.Partner.create({"name": "tracked"})
        # message_type defaults to 'notification' - the automated path.
        self.assertTrue(partner.with_user(self.user).message_post(body="sys"))

    def test_activity_scheduling_blocked(self):
        self._profile(model_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "hide_activity": True,
        })])
        partner = self.Partner.create({"name": "no activity"})
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        with self.assertRaises(AccessError):
            self.env["mail.activity"].with_user(self.user).create({
                "res_model_id": self.partner_model.id,
                "res_id": partner.id,
                "activity_type_id": activity_type.id,
            })

    # -- print menu -------------------------------------------------------- #
    def test_hide_print_drops_every_report_binding(self):
        self._profile(model_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "hide_print": True,
        })])
        self.env["ir.actions.report"].create({
            "name": "AM Test Report",
            "model": "res.partner",
            "report_name": "am.test.report",
            "report_type": "qweb-pdf",
            "binding_model_id": self.partner_model.id,
            "binding_type": "report",
        })
        bindings = self.env["ir.actions.actions"].with_user(
            self.user).get_bindings("res.partner")
        self.assertNotIn("report", bindings)

    # -- default profile for new users ------------------------------------ #
    def test_auto_assign_internal_user(self):
        profile = self._profile(user_ids=[(5, 0, 0)], auto_assign="internal")
        newcomer = new_test_user(
            self.env, login="am_new_internal", groups="base.group_user")
        self.assertIn(newcomer, profile.user_ids)

    def test_auto_assign_portal_user_skips_internal(self):
        profile = self._profile(user_ids=[(5, 0, 0)], auto_assign="portal")
        internal = new_test_user(
            self.env, login="am_new_internal2", groups="base.group_user")
        portal = new_test_user(
            self.env, login="am_new_portal", groups="base.group_portal")
        self.assertNotIn(internal, profile.user_ids)
        self.assertIn(portal, profile.user_ids)

    def test_user_inspection(self):
        self._profile(field_rule_ids=[(0, 0, {
            "model_id": self.partner_model.id,
            "field_id": self.phone_field.id, "mode": "invisible"})])
        result = self.Profile.get_user_inspection(self.user.id)
        self.assertEqual(result["user"]["id"], self.user.id)
        self.assertGreaterEqual(result["counts"]["rules"], 1)
        self.assertGreaterEqual(result["counts"]["fields"], 1)
