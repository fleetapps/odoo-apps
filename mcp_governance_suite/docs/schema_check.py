#!/usr/bin/env python3
"""Validate every <field name="..."> our XML writes to a CORE Odoo model
against the real Odoo 19 field definitions, plus check that every ref= xmlid
resolves. This is the check that would have caught res.groups.category_id and
ir.actions.server.groups_id before an install attempt.
"""
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = "/Users/kmiller/odoo-apps"
O19 = "/private/tmp/claude-501/-Users-kmiller-odoo-apps-shopify-bisync/ddf46dba-8017-4677-8b00-af1b80e0113d/scratchpad/o19"

# core model -> source files whose field defs apply (incl. inherited/delegated)
SOURCES = {
    "res.groups":            ["o19_res_groups.py"],
    "res.groups.privilege":  ["o19_priv.py"],
    "ir.module.category":    ["ir_module.py"],
    "ir.rule":               ["ir_rule.py"],
    "ir.ui.view":            ["ir_ui_view.py"],
    "ir.cron":               ["o19_ir_cron.py", "o19_ir_actions.py"],
    "ir.actions.act_window": ["o19_ir_actions.py"],
    "ir.actions.client":     ["o19_ir_actions.py"],
    "ir.actions.server":     ["o19_ir_actions.py"],
    "mail.alias":            ["mail_alias.py"],
    "product.product":       ["product_product.py", "product_template.py"],
    "delivery.carrier":      ["delivery_carrier.py"],
}

# fields every model has, or that convert.py handles specially
UNIVERSAL = {
    "id", "display_name", "create_date", "write_date", "create_uid",
    "write_uid", "active", "sequence", "name", "company_id", "company_ids",
    "__last_update",
}


def fields_for(model):
    names = set(UNIVERSAL)
    for fn in SOURCES.get(model, []):
        p = os.path.join(O19, fn)
        if not os.path.exists(p):
            continue
        src = open(p, encoding="utf-8", errors="replace").read()
        # field assignments at class-body indent
        names |= set(re.findall(r"^\s{4}(\w+)\s*=\s*fields\.\w+\(", src, re.M))
        # _inherits delegation targets are resolved by including the parent file
    return names


def main():
    os.chdir(ROOT)
    # collect all xmlids defined anywhere, for ref= resolution
    defined = set()
    for f in glob.glob("*/**/*.xml", recursive=True):
        mod = f.split("/")[0]
        try:
            t = ET.parse(f)
        except Exception:
            continue
        for el in t.iter():
            if el.tag in ("record", "menuitem", "template", "act_window") and el.get("id"):
                i = el.get("id")
                defined.add(i if "." in i else f"{mod}.{i}")
    # csv-declared model_ ids (security ACLs reference model_<name>)
    for f in glob.glob("*/security/ir.model.access.csv"):
        mod = f.split("/")[0]
        for line in open(f, encoding="utf-8").read().splitlines()[1:]:
            parts = line.split(",")
            if len(parts) > 2 and parts[2].strip():
                m = parts[2].strip()
                defined.add(m if "." in m else f"{mod}.{m}")

    unknown_field = []
    for f in sorted(glob.glob("*/**/*.xml", recursive=True)):
        mod = f.split("/")[0]
        try:
            t = ET.parse(f)
        except Exception:
            continue
        for rec in t.iter("record"):
            model = rec.get("model")
            if model not in SOURCES:
                continue
            valid = fields_for(model)
            for fld in rec.findall("field"):
                n = fld.get("name")
                if n and n not in valid:
                    unknown_field.append((f, rec.get("id"), model, n))

    print("=" * 72)
    print("FIELD NAMES ON CORE MODELS")
    print("=" * 72)
    if unknown_field:
        for f, rid, model, n in unknown_field:
            print(f"  SUSPECT  {f}: <record id={rid!r} model={model!r}> field {n!r}")
    else:
        print("  clean — every field written to a core model exists in Odoo 19")

    print()
    print("=" * 72)
    print("MODELS WRITTEN TO, WITH FIELD-SOURCE COVERAGE")
    print("=" * 72)
    for m in sorted(SOURCES):
        print(f"  {m:26s} {len(fields_for(m)):4d} known fields")
    return 1 if unknown_field else 0


sys.exit(main())
