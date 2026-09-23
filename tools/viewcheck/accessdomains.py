#!/usr/bin/env python3
"""Validate every ir.access.csv domain against the model it targets.

ir.access._check_domain (odoo/addons/base/models/ir_access.py) does, at install:

    domain = safe_eval(access.domain, {'user','time','company_ids','company_id'})
    Domain(domain).map_conditions(skip_access).validate(self.env[model].sudo())

A field that does not exist -> ValidationError -> the install fails. A field that
exists but is a non-stored compute/related with no search= is unsearchable, which
Domain.validate may accept but the query will not, so both are reported.
"""
import ast, csv, os, re, sys

OPS = {"=", "!=", "in", "not in", "<", "<=", ">", ">=", "like", "ilike",
       "not like", "not ilike", "=like", "=ilike", "child_of", "parent_of",
       "any", "not any", "access"}


def model_fields(root):
    """{model: {field: (stored, has_search)}} — same parser as viewcheck."""
    out = {}
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if os.path.basename(dp) not in ("models", "wizard", "wizards"):
            continue
        for f in fs:
            if not f.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(dp, f)).read())
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                name, inherit, fields = None, None, {}
                for stmt in cls.body:
                    if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                        tgt, value = stmt.target, stmt.value
                    elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                        tgt, value = stmt.targets[0], stmt.value
                    else:
                        continue
                    if not isinstance(tgt, ast.Name):
                        continue
                    if tgt.id == "_name" and isinstance(value, ast.Constant):
                        name = value.value; continue
                    if tgt.id == "_inherit" and isinstance(value, ast.Constant):
                        inherit = value.value; continue
                    if not isinstance(value, ast.Call):
                        continue
                    fn = value.func
                    if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                            and fn.value.id == "fields"):
                        continue
                    kw = {k.arg for k in value.keywords}
                    vals = {k.arg: k.value for k in value.keywords}
                    computed = {"compute", "related"} & kw
                    stored = (not computed) or (
                        isinstance(vals.get("store"), ast.Constant)
                        and vals["store"].value is True)
                    fields[tgt.id] = (bool(stored), "search" in kw)
                key = name or inherit
                if key:
                    out.setdefault(key, {}).update(fields)
    return out


# Fields the ORM provides on every model.
BUILTIN = {"id", "create_uid", "create_date", "write_uid", "write_date",
           "display_name", "active", "__last_update"}


def fields_in_domain(dom_src):
    """Field paths on the left of each condition triple."""
    # Replace the eval-context names so literal_eval can parse the structure.
    src = re.sub(r"\buser\.[A-Za-z_.]+", "0", dom_src)
    src = re.sub(r"\bcompany_ids\b|\bcompany_id\b(?!')", "[]", src)
    src = re.sub(r"\btime\.[A-Za-z_.()]+", "0", src)
    try:
        parsed = ast.literal_eval(src)
    except Exception as e:
        return None, f"cannot parse: {e}"
    out = []
    for term in parsed:
        if isinstance(term, str):
            continue  # '&' '|' '!'
        if isinstance(term, (list, tuple)) and len(term) == 3:
            if term == (1, "=", 1) or term == [1, "=", 1]:
                continue
            if isinstance(term[0], str):
                out.append((term[0], term[1]))
    return out, None


def main(modules):
    allf = {}
    for m in modules:
        allf.update(model_fields(m))
    problems = []
    checked = 0
    for m in modules:
        p = f"{m}/security/ir.access.csv"
        if not os.path.exists(p):
            continue
        # model xmlid -> technical name, from `model_<underscored>`
        for r in csv.DictReader(open(p)):
            dom = (r.get("domain") or "").strip()
            if not dom:
                continue
            checked += 1
            mx = r["model_id:id"]
            tech = mx[len("model_"):].replace("_", ".") if mx.startswith("model_") else mx
            # the underscore->dot guess is ambiguous; resolve against known models
            cands = [k for k in allf if k.replace(".", "_") == mx[len("model_"):]]
            model = cands[0] if cands else tech
            paths, err = fields_in_domain(dom)
            if err:
                problems.append(f"{m}: {r['id']}: {err}"); continue
            if model not in allf:
                problems.append(f"{m}: {r['id']}: model {model!r} not found in parsed sources")
                continue
            for path, op in paths:
                base = path.split(".")[0]
                if base in BUILTIN:
                    continue
                if base not in allf[model]:
                    problems.append(
                        f"{m}: {r['id']}: {model}.{base} DOES NOT EXIST "
                        f"-> ir.access._check_domain raises at install")
                    continue
                stored, has_search = allf[model][base]
                if not stored and not has_search:
                    problems.append(
                        f"{m}: {r['id']}: {model}.{base} is non-stored with no search= "
                        f"-> unsearchable in an access domain")
    print(f"{checked} domain(s) checked across {len(modules)} module(s)")
    for x in problems:
        print("  " + x)
    if not problems:
        print("  -> every field referenced exists and is searchable")
    return problems


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1:]) else 0)
