"""Explain this change: why a P&L figure moved, in the P&L's side panel.

The breakdown is computed, not generated: the change of a line between two
periods is split into the change of each account (or, for an account, of each
partner), and the largest journal items are listed. Those figures always add
up and need no API key. The model then writes two or three sentences from
them, citing them by handle, so the narrative can only point at figures the
accountant can see and click.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.odin_account_pnl.models import odin_pnl_formula

from . import odin_ai_prompts as prompts
from .odin_ai_llm import final_json
from .odin_ai_tools import dumps

TOP_DRIVERS = 6
TOP_ITEMS = 5


class OdinAiExplain(models.AbstractModel):
    _name = "odin.ai.explain"
    _description = "Explain a P&L change"

    @api.model
    def explain(self, options, row_key, column_key="c0"):
        """The drivers of the change of ``row_key`` in ``column_key``
        against the period before (or the comparison already on screen)."""
        self._check()
        Report = self.env["odin.pnl.report"]
        ctx = Report._context(options)
        column = ctx.column(column_key or "c0")
        segments = row_key.split("/")
        kind = segments[-1].split(":")[0]
        if kind not in ("L", "A") or len(segments) > 2:
            raise UserError(_("Explain works on a P&L line or one of its accounts."))
        comparison = options.get("comparison") or {}
        mode = comparison.get("mode") if comparison.get("mode") in ("previous_period", "previous_year") else \
            "previous_period"
        explain_options = dict(
            ctx.options,
            date={"filter": "custom", "date_from": fields.Date.to_string(column["date_from"]),
                  "date_to": fields.Date.to_string(column["date_to"])},
            divide="none",
            comparison={"mode": mode, "periods": 1},
            trend=False,
            hide_zero=False,
            unfolded=[],
            modes={},
        )
        report = Report.get_report(dict(explain_options, unfolded=[segments[0]]))
        rows = {row["key"]: row for row in report["rows"]}
        row = rows.get(row_key)
        if not row:
            raise UserError(_("This row is not on the report for that period."))
        current_label, previous_label = report["columns"][0]["label"], report["columns"][1]["label"]
        current = row["values"].get("c0") or 0.0
        previous = row["values"].get("c1") or 0.0
        delta = current - previous

        if row["type"] == "formula":
            drivers = self._formula_drivers(report, row, delta)
            driver_kind = _("line")
        elif kind == "L":
            children = [r for r in report["rows"] if r["parent"] == row_key]
            drivers = self._drivers(children, delta)
            driver_kind = _("account")
        else:
            children = Report.get_children(report["options"], row_key, "partner", show_all=True)["rows"]
            drivers = self._drivers(children, delta)
            driver_kind = _("partner")
        second = []
        top = drivers[0] if drivers and drivers[0].get("key") else None
        if top and kind == "L":
            # Who moved the largest driver: its partners, against its own change.
            partners = Report.get_children(report["options"], top["key"], "partner", show_all=True)["rows"]
            second = self._drivers(partners, top["current"] - top["previous"], limit=3)
            for driver in second:
                driver["parent"] = top["name"]
        items = []
        if row["type"] != "formula":
            items = Report.get_children(report["options"], row_key, "audit:c0")["rows"][:TOP_ITEMS]
        handles = {}

        def handle(label, key=None, aml_id=None):
            ref = f"E{len(handles) + 1}"
            handles[ref] = {"label": label, "key": key, "aml_id": aml_id}
            return ref

        for driver in drivers + second:
            driver["ref"] = handle(driver["name"], key=driver.get("key"))
        item_rows = []
        for item in items:
            if item.get("type") != "entry":
                continue
            item_rows.append({
                "ref": handle(item["name"], aml_id=item.get("aml_id")),
                "name": item["name"],
                "date": item.get("date"),
                "partner": item.get("partner"),
                "label": item.get("label"),
                "amount": item["values"].get("c0"),
                "aml_id": item.get("aml_id"),
            })
        return {
            "row_key": row_key,
            "name": row["name"],
            "options": report["options"],
            "current_label": current_label,
            "previous_label": previous_label,
            "current": current,
            "previous": previous,
            "delta": delta,
            "pct": (delta / abs(previous) * 100) if previous else None,
            "good": None if not delta else (delta > 0) == bool(row.get("green_on_positive")),
            "driver_kind": driver_kind,
            "drivers": drivers,
            "second": second,
            "items": item_rows,
            "handles": handles,
            "ai": self.env["odin.ai.llm"].readiness() or False,
        }

    @api.model
    def narrate(self, explanation):
        """The model's sentences on the computed explanation."""
        self._check()
        facts = {
            "line": explanation["name"],
            "periods": [explanation["current_label"], explanation["previous_label"]],
            "current": explanation["current"],
            "previous": explanation["previous"],
            "change": explanation["delta"],
            "change_pct": explanation["pct"],
            "favourable": explanation["good"],
            "drivers": [
                {"ref": d["ref"], explanation["driver_kind"]: d["name"], "current": d["current"],
                 "previous": d["previous"], "effect_on_change": d["delta"], "share_of_change_pct": d["share"]}
                for d in explanation["drivers"]
            ],
            "within_top_driver": [
                {"ref": d["ref"], "partner": d["name"], "change": d["delta"]} for d in explanation["second"]
            ],
            "largest_items": [
                {"ref": i["ref"], "entry": i["name"], "date": i["date"], "partner": i["partner"],
                 "label": (i["label"] or "")[:200], "amount": i["amount"]}
                for i in explanation["items"]
            ],
        }
        company = self.env.company
        data = self.env["odin.ai.llm"].call(
            "explain",
            system=prompts.EXPLAIN_RULES.format(company=company.name, currency=company.currency_id.name),
            messages=[{"role": "user", "content": "Explain this change. Figures (data): " + dumps(facts)}],
            schema=prompts.EXPLAIN_SCHEMA,
        )
        answer = final_json(data)
        known = set(explanation["handles"])
        return {
            "headline": answer.get("headline") or "",
            "points": [
                {"text": point.get("text") or "",
                 "evidence": [ref for ref in point.get("evidence") or [] if ref in known]}
                for point in answer.get("points") or []
            ],
            "caveats": answer.get("caveats") or [],
        }

    @api.model
    def continue_in_ask(self, explanation):
        """Open Ask with the explanation as context of the first question."""
        self._check()
        summary = {
            "line": explanation["name"],
            "periods": [explanation["current_label"], explanation["previous_label"]],
            "current": explanation["current"],
            "previous": explanation["previous"],
            "drivers": [{"name": d["name"], "change": d["delta"]} for d in explanation["drivers"]],
        }
        return {
            "type": "ir.actions.client",
            "tag": "odin_account_ai.ask",
            "name": _("Ask"),
            "params": {
                "question": _("Why did %(line)s change between %(previous)s and %(current)s?",
                              line=explanation["name"], previous=explanation["previous_label"],
                              current=explanation["current_label"]),
                "context": summary,
            },
        }

    def _formula_drivers(self, report, row, delta):
        """The change of a formula line (Gross Profit = REV - COS) split by
        the account lines it is built from, through formulas of formulas.

        Each line's effect is the change of the formula when that line alone
        moves from the previous to the current period; for sums and
        differences these add up exactly to the change. A formula with
        products or ratios leaves a remainder, shown as its own row so the
        total still matches."""
        layout = self.env["odin.pnl.layout"].browse(report["options"]["layout_id"])
        parsed = layout._parsed_lines()
        formulas = {entry["line"].code: entry["formula"] for entry in parsed
                    if entry["line"].kind == "formula" and entry["formula"] is not None}
        rows = {r["code"]: r for r in report["rows"] if r["parent"] is None and r.get("code")}
        current = {code: (r["values"].get("c0") or 0.0) for code, r in rows.items()}
        previous = {code: (r["values"].get("c1") or 0.0) for code, r in rows.items()}

        def calc(code, values, stack=()):
            if code in formulas and code not in stack:
                tree = formulas[code]
                return odin_pnl_formula.evaluate(
                    tree, {name: calc(name, values, stack + (code,)) for name in odin_pnl_formula.codes(tree)})
            return values.get(code, 0.0)

        def leaves(code, stack=()):
            if code not in formulas or code in stack:
                return {code}
            return set().union(*(leaves(name, stack + (code,)) for name in odin_pnl_formula.codes(formulas[code])))

        base = calc(row["code"], previous)
        drivers = []
        for code in sorted(leaves(row["code"])):
            if code not in rows:
                continue
            moved = calc(row["code"], dict(previous, **{code: current.get(code, 0.0)}))
            effect = None if moved is None or base is None else moved - base
            if effect is None or abs(effect) < 0.005:
                continue
            drivers.append({"key": rows[code]["key"], "name": rows[code]["name"], "code": code,
                            "current": current.get(code, 0.0), "previous": previous.get(code, 0.0),
                            "delta": effect})
        drivers.sort(key=lambda driver: -abs(driver["delta"]))
        rest = delta - sum(driver["delta"] for driver in drivers)
        if abs(rest) >= 0.005:
            drivers.append({"key": None, "name": _("Combined effect"), "code": "",
                            "current": 0.0, "previous": 0.0, "delta": rest})
        for driver in drivers:
            driver["share"] = (driver["delta"] / delta * 100) if delta else None
        return drivers

    def _drivers(self, children, delta, limit=TOP_DRIVERS):
        rows = []
        for child in children:
            if child.get("type") not in ("account", "breakdown", "group"):
                continue
            current = child["values"].get("c0") or 0.0
            previous = child["values"].get("c1") or 0.0
            change = current - previous
            if abs(change) < 0.005:
                continue
            rows.append({"key": child["key"], "name": child["name"], "code": child.get("code") or "",
                         "current": current, "previous": previous, "delta": change})
        rows.sort(key=lambda row: -abs(row["delta"]))
        top, rest = rows[:limit], rows[limit:]
        if rest:
            rows = top + [{
                "key": None, "name": _("%(count)s others", count=len(rest)), "code": "",
                "current": sum(r["current"] for r in rest), "previous": sum(r["previous"] for r in rest),
                "delta": sum(r["delta"] for r in rest)}]
        else:
            rows = top
        for row in rows:
            row["share"] = (row["delta"] / delta * 100) if delta else None
        return rows

    @api.model
    def _check(self):
        if not self.env.user.has_group("odin_account_ai.group_ai_user") or \
                not self.env.user.has_group("account.group_account_readonly"):
            raise AccessError(_("Explain is for users who can see accounting."))
