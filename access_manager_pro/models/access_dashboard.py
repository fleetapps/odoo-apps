# -*- coding: utf-8 -*-
"""Dashboard data provider.

These read-only methods live on ``access.manager.profile`` (so they inherit its
admin-only ACL and are callable from the OWL client via ``orm.call``). They
aggregate the profiles and their rule lines into the compact payloads the
Access Management dashboard renders. Everything runs ``sudo`` because the caller
is already an Access Manager administrator and we want counts across companies.
"""

from collections import Counter
from datetime import timedelta

from odoo import api, fields, models


class AccessDashboard(models.Model):
    _inherit = "access.manager.profile"

    # ------------------------------------------------------------------ #
    #  Overview payload
    # ------------------------------------------------------------------ #
    @api.model
    def get_dashboard_data(self):
        profiles = self.sudo().with_context(active_test=False).search([])
        now = fields.Datetime.now()
        soon = now + timedelta(days=7)
        today = fields.Datetime.to_datetime(fields.Date.today())

        active = profiles.filtered("active")
        inactive = profiles - active
        expiring = active.filtered(lambda p: p.date_end and now <= p.date_end <= soon)
        expired = profiles.filtered(lambda p: p.date_end and p.date_end < now)
        expired_today = expired.filtered(lambda p: p.date_end >= today)
        with_user = profiles.filtered("user_ids")
        group_only = profiles.filtered(lambda p: p.group_ids and not p.user_ids)
        time_based = profiles.filtered("restriction_time_based")
        login_off = profiles.filtered("disable_login")

        total = len(profiles) or 1
        prior = self.sudo().with_context(active_test=False).search_count(
            [("create_date", ">=", now - timedelta(days=60)),
             ("create_date", "<", now - timedelta(days=30))])
        recent = self.sudo().with_context(active_test=False).search_count(
            [("create_date", ">=", now - timedelta(days=30))])

        def pct(n):
            return round(n / total * 100)

        kpis = {
            "total": len(profiles),
            "active": len(active), "active_pct": pct(len(active)),
            "inactive": len(inactive), "inactive_pct": pct(len(inactive)),
            "expiring": len(expiring), "expiring_pct": pct(len(expiring)),
            "expired_today": len(expired_today), "expired_pct": pct(len(expired)),
            "user_rules": len(with_user), "user_pct": pct(len(with_user)),
            "group_rules": len(group_only), "group_pct": pct(len(group_only)),
            "time_based": len(time_based), "time_pct": pct(len(time_based)),
            "login_disabled": len(login_off), "login_pct": pct(len(login_off)),
            "trend": round(recent / prior, 1) if prior else None,
        }

        affecting = self._affecting_counts(active)

        return {
            "company": {"id": self.env.company.id, "name": self.env.company.name},
            "updated": fields.Datetime.context_timestamp(self, now).strftime("%H:%M"),
            "kpis": kpis,
            "restriction_split": self._restriction_split(profiles),
            "created_series": self._created_series(profiles, now),
            "restricted_models": self._restricted_models(),
            "insights": self._insights(profiles, active, expired),
            "heatmap": self._heatmap(),
            # Research-backed access-dashboard charts:
            "restriction_load": self._restriction_load(affecting),      # per-user distribution
            "top_users": self._top_users(affecting),                    # over-restricted outliers
            "by_company": self._by_company(active),                     # access by company
            "expirations": self._expirations(active, now),              # temporary/expiring access
            "rule_mix": self._rule_mix(profiles),                       # restriction composition
            "users": [
                {"id": u.id, "name": u.name, "login": u.login}
                for u in self.env["res.users"].sudo().search(
                    [("share", "=", False), ("active", "=", True)],
                    order="name", limit=200)
            ],
        }

    # ------------------------------------------------------------------ #
    #  Chart series
    # ------------------------------------------------------------------ #
    def _restriction_split(self, profiles):
        user = len(profiles.filtered(lambda p: p.restriction_type == "user"))
        group = len(profiles.filtered(
            lambda p: p.restriction_type in ("group", "mixed")))
        return {"user": user, "group": group}

    def _created_series(self, profiles, now):
        buckets, labels = [], []
        year, month = now.year, now.month
        months = []
        for _i in range(5, -1, -1):
            m = month - _i
            y = year
            while m <= 0:
                m += 12
                y -= 1
            months.append((y, m))
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        counts = Counter((p.create_date.year, p.create_date.month)
                         for p in profiles if p.create_date)
        for (y, m) in months:
            labels.append(names[m - 1])
            buckets.append(counts.get((y, m), 0))
        return [{"label": l, "value": v} for l, v in zip(labels, buckets)]

    def _restricted_models(self, limit=10):
        counter = Counter()
        for model in ("access.manager.model.rule", "access.manager.field.rule",
                      "access.manager.element.rule", "access.manager.domain.rule"):
            for name in self.env[model].sudo().search([]).mapped("model_name"):
                if name:
                    counter[name] += 1
        top = counter.most_common(limit)
        return [{"model": name, "count": count} for name, count in top]

    # ------------------------------------------------------------------ #
    #  Per-user distribution / outliers / company / expirations / mix
    # ------------------------------------------------------------------ #
    def _affecting_counts(self, profiles):
        """{user_id: number of active profiles affecting them} (direct + group)."""
        counter = Counter()
        for p in profiles:
            uids = set(p.user_ids.ids) | set(p.group_ids.users.ids)
            for uid in uids:
                counter[uid] += 1
        return counter

    def _restriction_load(self, counter):
        """Histogram: how many users fall in each 'rules affecting them' band."""
        bands = [("1–2", 1, 2), ("3–5", 3, 5), ("6–10", 6, 10), ("11+", 11, 10 ** 9)]
        result = [{"label": lbl, "value": 0} for lbl, _lo, _hi in bands]
        for count in counter.values():
            for i, (_lbl, lo, hi) in enumerate(bands):
                if lo <= count <= hi:
                    result[i]["value"] += 1
                    break
        return result

    def _top_users(self, counter, limit=8):
        """Most-affected (over-restricted) users."""
        top = counter.most_common(limit)
        names = {u.id: u.name for u in self.env["res.users"].sudo().browse(
            [uid for uid, _ in top])}
        return [{"name": names.get(uid, "?"), "count": count} for uid, count in top]

    def _by_company(self, active):
        """Active rules effective per company (company-scoped + global)."""
        companies = self.env["res.company"].sudo().search([])
        global_count = len(active.filtered(lambda p: not p.company_ids))
        rows = [{
            "company": c.name,
            "count": len(active.filtered(lambda p: c in p.company_ids)) + global_count,
        } for c in companies]
        rows.sort(key=lambda r: r["count"], reverse=True)
        return rows[:8]

    def _expirations(self, active, now):
        """Upcoming expirations bucketed by urgency."""
        bands = [("≤ 7 days", 7), ("8–30 days", 30), ("31–90 days", 90)]
        result = [{"label": lbl, "value": 0} for lbl, _ in bands] + [{"label": "> 90 days", "value": 0}]
        for p in active.filtered("date_end"):
            days = (p.date_end - now).days
            if days < 0:
                continue
            placed = False
            for i, (_lbl, hi) in enumerate(bands):
                if days <= hi:
                    result[i]["value"] += 1
                    placed = True
                    break
            if not placed:
                result[-1]["value"] += 1
        return result

    def _rule_mix(self, profiles):
        """Composition of restrictions by rule kind."""
        env = self.env
        user_global = len(profiles.filtered(
            lambda p: p.is_readonly_user or p.disable_login
            or p.disable_developer_mode or p.hide_import or p.hide_export
            or p.hide_chatter or p.hide_add_property or p.hide_search_panel))
        mix = [
            ("Menus", sum(len(p.hidden_menu_ids) for p in profiles)),
            ("Models", env["access.manager.model.rule"].sudo().search_count([])),
            ("Fields", env["access.manager.field.rule"].sudo().search_count([])),
            ("Buttons/Tabs", env["access.manager.element.rule"].sudo().search_count([])),
            ("Records", env["access.manager.domain.rule"].sudo().search_count([])),
            ("User/Global", user_global),
        ]
        return [{"label": lbl, "value": val} for lbl, val in mix]

    # ------------------------------------------------------------------ #
    #  Insights
    # ------------------------------------------------------------------ #
    def _insights(self, profiles, active, expired):
        dead = len(active.filtered(lambda p: p.restriction_type == "none"))
        expired_active = len(active & expired)
        score = max(40, 100 - dead * 6 - expired_active * 4)
        rating = "Good" if score >= 80 else "Fair" if score >= 60 else "Review"

        companies = self.env["res.company"].sudo().search([])
        if active.filtered(lambda p: not p.company_ids):
            covered = len(companies)
        else:
            covered = len(active.mapped("company_ids"))

        models = set()
        for model in ("access.manager.model.rule", "access.manager.field.rule",
                      "access.manager.element.rule", "access.manager.domain.rule"):
            models |= set(self.env[model].sudo().search([]).mapped("model_name"))

        affected = set(active.mapped("user_ids").ids)
        affected |= set(active.mapped("group_ids.users").ids)

        return {
            "config_score": int(score),
            "rating": rating,
            "admin_protected": True,
            "companies_total": len(companies),
            "companies_covered": covered,
            "cache_status": "Fresh",
            "models_covered": len({m for m in models if m}),
            "users_affected": len(affected),
        }

    # ------------------------------------------------------------------ #
    #  Heatmap
    # ------------------------------------------------------------------ #
    def _heatmap(self):
        actions = ["read", "write", "create", "delete", "readonly",
                   "field_hide", "view_hide"]
        cells = {}

        def bucket(model):
            return cells.setdefault(model, dict.fromkeys(actions, 0))

        for rule in self.env["access.manager.model.rule"].sudo().search([]):
            if not rule.model_name:
                continue
            c = bucket(rule.model_name)
            c["create"] += int(rule.hide_create)
            c["write"] += int(rule.hide_edit)
            c["delete"] += int(rule.hide_delete)
            c["readonly"] += int(rule.readonly)
            c["view_hide"] += len(rule._hidden_view_modes())
        for rule in self.env["access.manager.field.rule"].sudo().search([]):
            if rule.model_name:
                bucket(rule.model_name)["field_hide"] += 1
        for rule in self.env["access.manager.element.rule"].sudo().search([]):
            if rule.model_name:
                bucket(rule.model_name)["view_hide"] += 1
        for rule in self.env["access.manager.domain.rule"].sudo().search([]):
            if not rule.model_name:
                continue
            c = bucket(rule.model_name)
            c["read"] += int(rule.perm_read)
            c["write"] += int(rule.perm_write)
            c["create"] += int(rule.perm_create)
            c["delete"] += int(rule.perm_unlink)

        rows = []
        for model, c in cells.items():
            total = sum(c.values())
            rows.append({"model": model, "cells": c, "total": total})
        rows.sort(key=lambda r: r["total"], reverse=True)
        return {"actions": actions, "models": rows}

    # ------------------------------------------------------------------ #
    #  User inspector
    # ------------------------------------------------------------------ #
    @api.model
    def get_user_inspection(self, user_id):
        user = self.env["res.users"].sudo().browse(user_id)
        if not user.exists():
            return {}
        group_ids = self._user_group_ids(user)
        matching = self.sudo().with_context(active_test=False).search([
            "|", ("user_ids", "in", user.id), ("group_ids", "in", group_ids),
        ])
        in_effect = self._profiles_for(user, self.env.company)

        rules = []
        for p in matching:
            if user in p.user_ids:
                rtype = "User"
            elif p.group_ids:
                rtype = "Group (%s)" % p.group_ids[0].name
            else:
                rtype = "—"
            if p.restriction_time_based:
                status, expiry = "Time", "%.2f - %.2f" % (p.time_start, p.time_end)
            elif p.state == "expiring":
                status = "Expiring"
                expiry = fields.Date.to_string(p.date_end) if p.date_end else "—"
            elif p.state == "expired":
                status, expiry = "Expired", fields.Date.to_string(p.date_end)
            else:
                status = p.state.capitalize() if p.state else "Active"
                expiry = fields.Date.to_string(p.date_end) if p.date_end else "—"
            rules.append({
                "id": p.id, "name": p.name, "type": rtype,
                "status": status, "expiry": expiry, "active": p.active,
            })

        menus = matching.mapped("hidden_menu_ids")
        field_rules = matching.mapped("field_rule_ids")
        fields_list = [{
            "model": fr.model_name, "field": fr.field_name, "mode": fr.mode,
        } for fr in field_rules]

        return {
            "user": {"id": user.id, "name": user.name, "login": user.login},
            "counts": {
                "rules": len(matching),
                "menus": len(menus),
                "fields": len(field_rules),
                "effective": len(in_effect),
            },
            "rules": rules,
            "menus": [{"id": m.id, "name": m.complete_name or m.name} for m in menus],
            "fields": fields_list,
        }
