#!/usr/bin/env python3
"""Reproduce Odoo's install-time RelaxNG view validation offline, with xmllint.

Mirrors odoo/tools/view_validation.py::schema_valid: the same six view types are
schema-validated, and form views are not validated at all.
"""
import os, subprocess, sys, tempfile
import xml.etree.ElementTree as ET

RNG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rng")
VALIDATED = ("search", "list", "calendar", "graph", "pivot", "activity")

def check(path):
    problems = []
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        return [(path, "-", f"XML parse error: {e}")]
    for rec in tree.iter("record"):
        if rec.get("model") != "ir.ui.view":
            continue
        arch = rec.find("./field[@name='arch']")
        if arch is None:
            continue
        for node in list(arch):
            if node.tag not in VALIDATED:
                continue
            rng = os.path.join(RNG_DIR, f"{node.tag}_view.rng")
            with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
                fh.write(ET.tostring(node, encoding="unicode"))
                tmp = fh.name
            try:
                r = subprocess.run(["xmllint", "--noout", "--relaxng", rng, tmp],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    msg = "\n".join(l for l in r.stderr.splitlines()
                                    if "validates" not in l).strip()
                    problems.append((path, rec.get("id", "?"), msg))
            finally:
                os.unlink(tmp)
    return problems

def main(roots):
    found = []
    n = 0
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            # Skip dot-directories in place. This repo keeps a git worktree at
            # shopify_bisync/.claude/worktrees/ holding a second copy of every
            # module; scanning it doubles the work and reports confusing paths.
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.endswith(".xml"):
                    n += 1
                    found += check(os.path.join(dirpath, f))
    for path, vid, msg in found:
        print(f"\n--- {path}  [{vid}]\n{msg}")
    print(f"\n{n} XML files scanned, {len(found)} schema failure(s)")
    return 1 if found else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["."]))
