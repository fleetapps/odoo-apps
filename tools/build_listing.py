#!/usr/bin/env python3
"""Prepare an Odoo Apps Store description for the store's sanitizer.

The store strips <style>/<link> from description HTML - verified against the
top-downloaded third-party listings, which all carry zero <style> tags and
hundreds of inline style attributes. So every rule has to be inlined.

It also decodes index.html as latin-1 when the file declares no charset,
turning em dashes and tick marks into mojibake. Emitting pure ASCII with HTML
entities makes the file decode identically under any encoding.
"""
import logging, re, sys
from premailer import Premailer
from lxml import html as LH
from cssselect import GenericTranslator, SelectorError

BREAKPOINT = 640.0          # max-width the source media queries used
PSEUDO = re.compile(r':(?:first|last|only|nth)-(?:child|of-type)')


# --------------------------------------------------------------- css helpers
def parse_rules(css):
    """[(selector_text, {prop: value}), ...] in source order."""
    rules = []
    for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
        decls = {}
        for d in body.split(';'):
            if ':' in d:
                p, _, v = d.partition(':')
                decls[p.strip().lower()] = v.strip()
        if decls:
            rules.append((sel.strip(), decls))
    return rules


def render_rules(rules):
    return '\n'.join('%s{%s}' % (s, ';'.join('%s:%s' % kv for kv in d.items()))
                     for s, d in rules)


def build_var_map(css):
    """Custom properties, with references to other properties resolved."""
    vars_ = {k: v.strip() for k, v in
             re.findall(r'(--[\w-]+)\s*:\s*([^;}]+)', css)}
    for _ in range(10):
        changed = False
        for k, v in list(vars_.items()):
            new = expand_vars(v, vars_, recurse=False)
            if new != v:
                vars_[k], changed = new, True
        if not changed:
            break
    return vars_


def expand_vars(text, vars_, recurse=True):
    """Replace var(--x[, fallback]) with the literal value.

    Inlining loses the rule that defined --x, so any surviving var() would
    resolve to nothing. Handles nested fallbacks like var(--a, var(--b)).
    """
    pat = re.compile(r'var\(\s*(--[\w-]+)\s*(,\s*([^()]*(?:\([^()]*\)[^()]*)*))?\)')

    def sub(m):
        name, fallback = m.group(1), m.group(3)
        if name in vars_:
            return vars_[name]
        return fallback.strip() if fallback else 'inherit'

    for _ in range(10 if recurse else 1):
        new = pat.sub(sub, text)
        if new == text:
            break
        text = new
    return text


def strip_var_defs(css):
    return re.sub(r'--[\w-]+\s*:\s*[^;}]+;?', '', css)


def split_media(css):
    """Pull @media blocks out, returning (base_css, [block_bodies])."""
    blocks, out, i = [], [], 0
    for m in re.finditer(r'@media[^{]*\{', css):
        if m.start() < i:
            continue
        depth, j = 1, m.end()
        while depth and j < len(css):
            if css[j] == '{':
                depth += 1
            elif css[j] == '}':
                depth -= 1
            j += 1
        out.append(css[i:m.start()])
        blocks.append(css[m.end():j - 1])
        i = j
    out.append(css[i:])
    return ''.join(out), blocks


def fluid(mobile, desktop):
    """clamp() equal to `mobile` at the breakpoint, growing to `desktop`.

    A <n>vw preferred term hits the mobile value exactly at 640px, so this
    reproduces the old breakpoint as a smooth ramp with no @media needed.
    """
    mo, de = re.match(r'^(-?[\d.]+)px$', mobile), re.match(r'^(-?[\d.]+)px$', desktop)
    if not (mo and de):
        return None
    m, d = float(mo.group(1)), float(de.group(1))
    if m >= d or m <= 0:
        return None
    return 'clamp(%gpx,%gvw,%gpx)' % (m, round(m / BREAKPOINT * 100, 3), d)


def fold_media(base_rules, blocks):
    """Merge max-width media rules into base rules as clamp() values."""
    folded, unfolded = 0, []
    for blk in blocks:
        for sel, decls in parse_rules(blk):
            targets = [s.strip() for s in sel.split(',')]
            for prop, mob in decls.items():
                for t in targets:
                    hit = False
                    for bsel, bdecls in base_rules:
                        if t not in [s.strip() for s in bsel.split(',')]:
                            continue
                        if prop not in bdecls:
                            continue
                        desk, mobp = bdecls[prop].split(), mob.split()
                        if len(desk) != len(mobp):
                            continue
                        new = [fluid(mp, dp) or dp for mp, dp in zip(mobp, desk)]
                        if any(n.startswith('clamp') for n in new):
                            bdecls[prop] = ' '.join(new)
                            folded, hit = folded + 1, True
                    if not hit:
                        unfolded.append('%s{%s:%s}' % (t, prop, mob))
    return folded, unfolded


# ------------------------------------------------------------------ pipeline
def unwrap_document(out):
    """Return just the fragment premailer wrapped in <html><head>/<body>."""
    m = re.search(r'<body[^>]*>(.*)</body>', out, re.S)
    if m:
        return m.group(1)
    m = re.search(r'<head[^>]*>(.*)</head>', out, re.S)
    if m:
        return m.group(1)
    return re.sub(r'</?(?:html|head|body)[^>]*>', '', out)


def structural_match(el, kind):
    """Evaluate the structural pseudo-classes cssselect will not translate."""
    parent = el.getparent()
    if parent is None:
        return False
    kids = [c for c in parent if isinstance(c.tag, str)]
    same = [c for c in kids if c.tag == el.tag]
    return {
        'first-child': kids and kids[0] is el,
        'last-child': kids and kids[-1] is el,
        'only-child': len(kids) == 1 and kids[0] is el,
        'first-of-type': same and same[0] is el,
        'last-of-type': same and same[-1] is el,
        'only-of-type': len(same) == 1 and same[0] is el,
    }.get(kind, False)


def select(doc, sel, translator):
    """Elements matching `sel`, falling back for unsupported pseudo-classes."""
    try:
        return doc.xpath(translator.css_to_xpath(sel))
    except SelectorError:
        pass
    m = re.search(r':((?:first|last|only)-(?:child|of-type))$', sel)
    if not m:
        raise
    base = sel[:m.start()].strip() or '*'
    return [el for el in doc.xpath(translator.css_to_xpath(base))
            if structural_match(el, m.group(1))]


def apply_pseudo_rules(html, rules):
    """Apply :first-child/:first-of-type/etc rules with lxml.

    cssutils mis-parses :first-of-type and corrupts the surrounding rule, so
    these are held back from premailer and applied here instead - appended
    last so they win over the rules premailer already inlined.
    """
    if not rules:
        return html, []
    doc = LH.fragment_fromstring(html, create_parent='div')
    tr, failed = GenericTranslator(), []
    for sel, decls in rules:
        for one in (x.strip() for x in sel.split(',')):
            try:
                els = select(doc, one, tr)
            except SelectorError:
                failed.append(one)
                continue
            add = ';'.join('%s:%s' % kv for kv in decls.items())
            for el in els:
                cur = el.get('style', '').rstrip().rstrip(';')
                el.set('style', (cur + '; ' + add) if cur else add)
    inner = (doc.text or '') + ''.join(
        LH.tostring(c, encoding='unicode') for c in doc)
    return inner, failed


def to_entities(html):
    """Escape every non-ASCII char so the file decodes identically anywhere."""
    named = {'—': '&mdash;', '–': '&ndash;', '·': '&middot;',
             '✓': '&check;', '✗': '&#10007;', '’': '&rsquo;',
             '‘': '&lsquo;', '“': '&ldquo;', '”': '&rdquo;',
             '…': '&hellip;', ' ': '&nbsp;', '→': '&rarr;',
             '↔': '&harr;', '×': '&times;', '•': '&bull;'}
    return ''.join(named.get(c, c) if ord(c) < 128 or c in named
                   else '&#x%X;' % ord(c) for c in html)


def process(path):
    src = open(path, encoding='utf-8').read()
    styles = re.findall(r'<style[^>]*>(.*?)</style>', src, re.S)
    rep = {'path': path, 'inline': 0, 'media': 0, 'vars': 0,
           'pseudo_failed': [], 'unfolded': [], 'leftover': ''}
    html = re.sub(r'<style[^>]*>.*?</style>', '', src, flags=re.S)

    if styles:
        css = re.sub(r'/\*.*?\*/', '', '\n'.join(styles), flags=re.S)
        var_map = build_var_map(css)
        rep['vars'] = len(var_map)
        css = strip_var_defs(expand_vars(css, var_map))
        # inline style="" attributes in the source can use var() too
        html = re.sub(r'style="([^"]*)"',
                      lambda m: 'style="%s"' % expand_vars(m.group(1), var_map),
                      html)

        base_css, media = split_media(css)
        rules = parse_rules(base_css)
        rep['media'], rep['unfolded'] = fold_media(rules, media)

        plain = [(s, d) for s, d in rules if not PSEUDO.search(s)]
        pseudo = [(s, d) for s, d in rules if PSEUDO.search(s)]

        p = Premailer('<style>%s</style>\n%s' % (render_rules(plain), html),
                      base_url=None, remove_classes=False, keep_style_tags=False,
                      exclude_pseudoclasses=False, strip_important=False,
                      disable_validation=True, disable_link_rewrites=True,
                      disable_basic_attributes=['align', 'height', 'width',
                                                'bgcolor', 'valign'],
                      cssutils_logging_level=logging.CRITICAL, method='html')
        out = p.transform()
        html = unwrap_document(out)
        rep['leftover'] = '\n'.join(
            x.strip() for x in re.findall(r'<style[^>]*>(.*?)</style>', html, re.S)
            if x.strip())
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.S)
        html, rep['pseudo_failed'] = apply_pseudo_rules(html, pseudo)

    # No <meta charset> here, deliberately. It is head-only content, and the
    # store's parser keeps everything after it inside <head>, so its body
    # extraction returns nothing and the listing publishes blank. The entity
    # escaping below is what actually guarantees the encoding: the output is
    # pure ASCII, so it decodes identically whether the file is read as UTF-8,
    # latin-1 or ASCII. The meta tag added nothing.
    html = re.sub(r'^\s*<meta[^>]*charset[^>]*>\s*', '', html.strip(), flags=re.I)
    html = to_entities(html)

    rep['inline'] = len(re.findall(r'style="', html))
    rep['stray_var'] = sorted(set(re.findall(r'var\(--[\w-]+', html)))
    rep['non_ascii'] = sum(1 for c in html if ord(c) > 127)
    open(path, 'w', encoding='utf-8').write(html + '\n')
    return rep


if __name__ == '__main__':
    for path in sys.argv[1:]:
        r = process(path)
        print("%-24s inline=%-4d media=%-2d vars=%-3d non_ascii=%d"
              % (r['path'].split('/')[-3], r['inline'], r['media'],
                 r['vars'], r['non_ascii']))
        for label, val in (('LEFTOVER CSS', r['leftover']),
                           ('UNRESOLVED var()', ', '.join(r['stray_var'])),
                           ('BAD SELECTOR', ', '.join(r['pseudo_failed'])),
                           ('DROPPED @media', '; '.join(r['unfolded']))):
            if val:
                print("   !! %s: %s" % (label, val))
