#!/usr/bin/env python3
"""Prove 19 -> 20 access equivalence per principal, not per row.

The naive row-by-row diff cannot decide correctness, because under Odoo 19 a
group's effective access was an emergent product of two separate layers:

    model access : ANY ACL row for ANY group the user holds (implied included)
    record set   : OR(group rules for held groups)  if any, else TRUE
                   AND(global rules)

Under Odoo 20 it is one expression (odoo/orm/models.py::_access_domain):

    Domain.OR(permissions for held groups) & Domain.AND(restrictions)

with Domain.OR([]) = FALSE (DomainOr.ZERO) and Domain.AND([]) = TRUE.

So this evaluates both models for every declared group as the principal, for
every operation, and compares the resulting (allowed?, record-domain) pair.
TRUE is canonicalised so that '', '[]' and "[(1,'=',1)]" compare equal --
Domain.__new__ maps all three to _TRUE_DOMAIN (verified in domains.py:224-256).
"""
import csv, os, re, sys
import xml.etree.ElementTree as ET

OPS = ("read", "write", "create", "unlink")
CRUD = {"create": "c", "read": "r", "write": "u", "unlink": "d"}
TRUE = "TRUE"


def canon(d):
    d = re.sub(r"\s+", "", d or "").replace('"', "'")
    return TRUE if d in ("", "[]", "[(1,'=',1)]") else d


def implications(xml_paths):
    """group -> set of groups it implies, transitively."""
    direct = {}
    for p in xml_paths:
        for rec in ET.parse(p).iter("record"):
            if rec.get("model") != "res.groups":
                continue
            g = rec.get("id")
            f = rec.find("./field[@name='implied_ids']")
            if f is None:
                direct.setdefault(g, set())
                continue
            direct.setdefault(g, set()).update(re.findall(r"ref\('([^']+)'\)", f.get("eval") or ""))
    def close(g, seen=None):
        seen = seen or set()
        for h in direct.get(g, ()):
            if h not in seen:
                seen.add(h)
                close(h, seen)
        return seen
    return {g: {g} | close(g) for g in direct}


def short(g):
    """Local xmlids in one module's csv may be bare; normalise to bare name."""
    return g.split(".")[-1]


def old_effective(acl, grules, globs, held, model, op):
    model_ok = any(op in acl.get((model, g), set()) for g in held)
    if not model_ok:
        return (False, None)
    doms = [grules[(model, g)] for g in held if (model, g) in grules]
    rec = sorted({canon(d) for d in doms}) if doms else [TRUE]
    if TRUE in rec:
        rec = [TRUE]
    glob = sorted({canon(d) for d in globs.get(model, [])})
    return (True, (tuple(rec), tuple(glob)))


def new_effective(perms, restr, held, model, op):
    doms = [d for g in held
            for (ops, d) in [perms.get((model, g), (set(), None))]
            if op in ops]
    if not doms:
        return (False, None)
    rec = sorted({canon(d) for d in doms})
    if TRUE in rec:
        rec = [TRUE]
    glob = sorted({canon(d) for d in restr.get(model, [])})
    return (True, (tuple(rec), tuple(glob)))


def run(module, old_csv, new_csv, xmls):
    print(f"\n{'='*72}\n{module}\n{'='*72}")
    acl, grules, globs = {}, {}, {}
    for r in csv.DictReader(open(old_csv)):
        k = (short(r["model_id:id"]), short(r.get("group_id:id") or ""))
        acl[k] = acl.get(k, set()) | {o for o in OPS if r[f"perm_{o}"].strip() == "1"}
    for p in xmls:
        for rec in ET.parse(p).iter("record"):
            if rec.get("model") != "ir.rule":
                continue
            m = short(rec.find("./field[@name='model_id']").get("ref"))
            df = rec.find("./field[@name='domain_force']")
            dom = (df.text or "") if df is not None else ""
            gf = rec.find("./field[@name='groups']")
            if gf is None:
                globs.setdefault(m, []).append(dom)
            else:
                for g in re.findall(r"ref\('([^']+)'\)", gf.get("eval") or ""):
                    grules[(m, short(g))] = dom

    perms, restr = {}, {}
    for r in csv.DictReader(open(new_csv)):
        m = short(r["model_id:id"]); g = short(r.get("group_id:id") or "")
        ops = {o for o in OPS if CRUD[o] in r["operation"]}
        if g:
            perms[(m, g)] = (ops, r["domain"])
        else:
            restr.setdefault(m, []).append(r["domain"])

    impl = implications(xmls)
    principals = sorted({g for (_m, g) in list(acl) + list(perms) if g})
    models = sorted({m for (m, _g) in list(acl) + list(perms)})
    diffs = []
    for p in principals:
        held = {short(x) for x in impl.get(p, {p})} | {p}
        for m in models:
            for op in OPS:
                a = old_effective(acl, grules, globs, held, m, op)
                b = new_effective(perms, restr, held, m, op)
                if a != b:
                    diffs.append((p, m, op, a, b))
    print(f"{len(principals)} principals x {len(models)} models x 4 ops "
          f"= {len(principals)*len(models)*4} outcomes compared")
    for p, m, op, a, b in diffs:
        print(f"  DIFF {p} / {m} / {op}\n        19: {a}\n        20: {b}")
    if not diffs:
        print("  -> every outcome identical")
    return diffs


def old_files(module, repo, ref, tmp):
    """Extract a module's pre-port security files from a git ref."""
    import subprocess
    out = {}
    for name, path in (("csv", f"{module}/security/ir.model.access.csv"),):
        dst = os.path.join(tmp, f"{module}.csv")
        with open(dst, "wb") as fh:
            fh.write(subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"],
                                    check=True, capture_output=True).stdout)
        out[name] = dst
    return out


if __name__ == "__main__":
    import argparse, glob, os, subprocess, tempfile
    ap = argparse.ArgumentParser(
        description="Prove an ir.model.access+ir.rule -> ir.access conversion "
                    "preserves effective access, per principal.")
    ap.add_argument("modules", nargs="+")
    ap.add_argument("--ref", default="19.0",
                    help="git ref holding the pre-port files (default: 19.0)")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--extra-xml", action="append", default=[],
                    help="additional security xml from the old ref, for group "
                         "implications defined in a dependency (repeatable, "
                         "as module/path)")
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        def fetch(path, name):
            dst = os.path.join(tmp, name)
            r = subprocess.run(["git", "-C", a.repo, "show", f"{a.ref}:{path}"],
                               capture_output=True)
            if r.returncode:
                return None
            open(dst, "wb").write(r.stdout)
            return dst

        total = []
        for m in a.modules:
            old_csv = fetch(f"{m}/security/ir.model.access.csv", f"{m}.csv")
            if not old_csv:
                print(f"\n{m}: no ir.model.access.csv at {a.ref} - skipped")
                continue
            xmls = []
            for xp in subprocess.run(
                    ["git", "-C", a.repo, "ls-tree", "--name-only", "-r",
                     f"{a.ref}", f"{m}/security/"],
                    capture_output=True, text=True).stdout.split():
                if xp.endswith(".xml"):
                    d = fetch(xp, os.path.basename(xp))
                    if d:
                        xmls.append(d)
            for xp in a.extra_xml:
                d = fetch(xp, "extra_" + os.path.basename(xp))
                if d:
                    xmls.append(d)
            new_csv = os.path.join(a.repo, m, "security", "ir.access.csv")
            if not os.path.exists(new_csv):
                print(f"\n{m}: no ir.access.csv yet - not ported")
                continue
            total += run(m, old_csv, new_csv, xmls)

        print(f"\n{'='*72}\nTOTAL behavioural differences: {len(total)}")
        sys.exit(1 if total else 0)
