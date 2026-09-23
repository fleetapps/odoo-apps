#!/usr/bin/env python3
"""Catch `Unsearchable field "x" in domain of <filter>` before install.

RelaxNG does not see this class of failure: Odoo raises it from field
resolution, so rngcheck passes and the install still dies. A field used in a
searchable position (search filter domain, group_by, order, graph/pivot axis)
must be stored, or declare a `search=`.
"""
import ast, os, re, sys
import xml.etree.ElementTree as ET

def model_fields(root):
    """{model_name: {field: (stored, has_search)}} from */models/*.py."""
    out = {}
    for dp, _d, fs in os.walk(root):
        if os.path.basename(dp) not in ("models", "wizard"):
            continue
        for f in fs:
            if not f.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(dp, f)).read())
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                name = None
                fields = {}
                for stmt in cls.body:
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                        tgt = stmt.targets[0]
                        if not isinstance(tgt, ast.Name):
                            continue
                        if tgt.id == "_name" and isinstance(stmt.value, ast.Constant):
                            name = stmt.value.value
                        elif isinstance(stmt.value, ast.Call):
                            fn = stmt.value.func
                            if (isinstance(fn, ast.Attribute)
                                    and isinstance(fn.value, ast.Name)
                                    and fn.value.id == "fields"):
                                kw = {k.arg for k in stmt.value.keywords}
                                vals = {k.arg: k.value for k in stmt.value.keywords}
                                computed = {"compute", "related"} & kw
                                stored = (not computed) or (
                                    isinstance(vals.get("store"), ast.Constant)
                                    and vals["store"].value is True)
                                fields[tgt.id] = (bool(stored), "search" in kw)
                if name:
                    out[name] = fields
    return out

def check(root, fields_by_model):
    problems = []
    for dp, _d, fs in os.walk(root):
        for f in fs:
            if not f.endswith(".xml"):
                continue
            path = os.path.join(dp, f)
            for rec in ET.parse(path).iter("record"):
                if rec.get("model") != "ir.ui.view":
                    continue
                m = rec.find("./field[@name='model']")
                arch = rec.find("./field[@name='arch']")
                if m is None or arch is None or m.text not in fields_by_model:
                    continue
                known = fields_by_model[m.text]
                for node in arch.iter():
                    used = []
                    if node.tag == "filter":
                        used += re.findall(r"\(\s*['\"]([a-zA-Z0-9_]+)", node.get("domain") or "")
                        ctx = node.get("context") or ""
                        used += re.findall(r"['\"]group_by['\"]\s*:\s*['\"]([a-zA-Z0-9_.]+)", ctx)
                    if node.tag in ("graph", "pivot"):
                        # every <field> directly inside a graph/pivot is an axis
                        used += [c.get("name") for c in node
                                 if c.tag == "field" and c.get("name")]
                    if node.tag in ("list", "search") and node.get("default_order"):
                        used += [t.strip().split()[0] for t in
                                 node.get("default_order").split(",") if t.strip()]
                    for name in used:
                        base = name.split(".")[0].split(":")[0]
                        if base in known:
                            stored, has_search = known[base]
                            if not stored and not has_search:
                                problems.append((path, rec.get("id"), m.text, base))
    return problems

if __name__ == "__main__":
    roots = sys.argv[1:] or ["."]
    allf = {}
    for r in roots:
        allf.update(model_fields(r))
    bad = []
    for r in roots:
        bad += check(r, allf)
    for path, vid, model, field in bad:
        print(f"UNSEARCHABLE  {path} [{vid}] {model}.{field}")
    print(f"{len(allf)} models parsed, {len(bad)} unsearchable use(s)")
    sys.exit(1 if bad else 0)
