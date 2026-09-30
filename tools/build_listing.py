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


# The store does not just strip <style>; it also filters inline style
# declarations against a property whitelist. Anything outside it is dropped
# silently, which is how a gradient hero with white text published as white
# text on white. Derived empirically by diffing what we send against what
# survives on the published pages.
SAFE_PROPS = {
    'background-color', 'border', 'border-bottom', 'border-collapse',
    'border-radius', 'border-spacing', 'border-top', 'color', 'display',
    'float', 'font-family', 'font-size', 'font-style', 'font-weight',
    'height', 'letter-spacing', 'line-height', 'margin', 'margin-bottom',
    'margin-left', 'margin-right', 'margin-top', 'max-width', 'min-height',
    'min-width', 'opacity', 'padding', 'padding-bottom', 'padding-left',
    'padding-right', 'padding-top', 'text-align', 'text-decoration',
    'text-transform', 'vertical-align', 'white-space', 'width',
}

# Widths for n inline-block siblings, left short of an even split so the
# whitespace between inline-blocks has somewhere to go.
SPLIT = {1: '100%', 2: '48%', 3: '31.5%', 4: '23.5%', 5: '18.5%', 6: '15.5%'}

# Usable content width of a listing, for turning a flex-basis back into columns.
CONTENT_WIDTH = 1040.0


def parse_colour(text):
    """(r, g, b, a) for the first colour in `text`, or None."""
    m = re.search(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)', text)
    if m:
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), a)
    m = re.search(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b', text)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    return None


def _colours(value):
    """Every colour in a value, in order, each as (r, g, b, a)."""
    out = []
    for tok in re.finditer(r'rgba?\([^)]*\)|#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b', value):
        c = parse_colour(tok.group(0))
        if c:
            out.append(c)
    return out


def composite(fg, bg):
    """Flatten a translucent colour onto an opaque one.

    Alpha cannot survive either - a translucent white pill on a dark hero
    would otherwise flatten to solid white and swallow its own white text.
    """
    r, g, b, a = fg
    return tuple(int(round(fg[i] * a + bg[i] * (1 - a))) for i in range(3)) + (1.0,)


def _lum(c):
    f = []
    for v in c[:3]:
        v /= 255.0
        f.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def hexof(c):
    return '#%02x%02x%02x' % tuple(c[:3])


def flatten_background(value, parent_bg, text_colour=None):
    """One opaque colour standing in for any background value.

    The shorthand is not whitelisted and gradients cannot be expressed at all,
    so every background collapses to a single background-color, composited
    over whatever it actually sits on. For a gradient, take the stop that
    reads best against the element's own text rather than the mean - averaging
    a purple with a green just yields mud.
    """
    if re.search(r'\bnone\b|url\(', value):
        return None
    cols = [composite(c, parent_bg) for c in _colours(value)]
    if not cols:
        return None
    if len(cols) == 1:
        return hexof(cols[0])
    fg = parse_colour(text_colour or '') or (255, 255, 255, 1.0)
    return hexof(max(cols, key=lambda c: _contrast(c, fg)))


def decls(style):
    out = []
    for d in style.split(';'):
        if ':' in d:
            k, _, v = d.partition(':')
            out.append((k.strip().lower(), v.strip()))
    return out


def proof_element(el, dropped, parent_bg):
    """Filter one element's declarations to the whitelist. Returns its
    resolved opaque background, for its children to composite against."""
    style = el.get('style')
    if not style:
        return parent_bg
    own = dict(decls(style))
    resolved = parent_bg
    keep = []
    for k, v in decls(style):
        if k in ('background', 'background-color'):
            c = flatten_background(v, parent_bg, own.get('color'))
            if c:
                keep.append(('background-color', c))
                resolved = parse_colour(c)
            continue
        if k in ('border-left', 'border-right'):
            # Side borders are not whitelisted; keep the accent as a top rule
            # rather than losing it entirely.
            keep.append(('border-top', v))
            dropped[k] = dropped.get(k, 0) + 1
            continue
        if k in SAFE_PROPS:
            keep.append((k, v))
        else:
            dropped[k] = dropped.get(k, 0) + 1
    el.set('style', '; '.join('%s:%s' % kv for kv in keep))
    return resolved


INLINE_TAGS = {'span', 'a', 'b', 'strong', 'em', 'label', 'small', 'i'}


def _px(value):
    m = re.search(r'(\d+(?:\.\d+)?)px', value or '')
    return float(m.group(1)) if m else None


def unflex(root, dropped):
    """Rebuild flex layouts as inline-block, which the whitelist does allow.

    Leaving display:flex in place is worse than removing it: flex-wrap and gap
    are both stripped, so a wrapping row of chips becomes one unwrappable line
    that runs off the side of the page.
    """
    for el in root.xpath('.//*[@style]'):
        d = dict(decls(el.get('style')))
        disp = d.get('display', '')
        if 'flex' not in disp:
            continue
        kids = [c for c in el if isinstance(c.tag, str)]
        gap = _px(d.get('gap', '')) or 0

        kid_bases = [_px(dict(decls(c.get('style') or '')).get('flex', '')) for c in kids]
        is_grid = (len(kids) >= 2
                   and all('flex' in dict(decls(c.get('style') or '')) for c in kids)
                   and any(kid_bases))
        if is_grid:
            # a row of columns: recover the column count from the flex-basis,
            # since the row really wrapped rather than sitting on one line
            va = 'middle' if d.get('align-items') == 'center' else 'top'
            bases = [b for b in kid_bases if b]
            cols = max(1, min(len(kids), int(CONTENT_WIDTH // max(bases))))
            w = SPLIT.get(cols, '%.4g%%' % (100.0 / cols - 2))
            for c, basis in zip(kids, kid_bases):
                cd = dict(decls(c.get('style') or ''))
                cd.pop('flex', None)
                cd['display'] = 'inline-block'
                cd['vertical-align'] = va
                # flex:0 0 auto meant "size to content" - it is not a column
                if basis:
                    cd.setdefault('width', w)
                c.set('style', '; '.join('%s:%s' % kv for kv in cd.items()))
        else:
            # a row of items, or a chip aligning its own contents
            for c in kids:
                cd = dict(decls(c.get('style') or ''))
                cd['display'] = ('inline-block'
                                 if 'flex' in cd.get('display', '')
                                 or c.tag in INLINE_TAGS
                                 or cd.get('display') in (None, '', 'block')
                                 else cd['display'])
                cd.setdefault('vertical-align', 'middle')
                if gap:
                    cd['margin-right'] = '%gpx' % gap
                    # vertical gap only where the row can actually wrap;
                    # on a single inline marker it just pads the line
                    if len(kids) >= 3:
                        cd['margin-bottom'] = '%gpx' % gap
                c.set('style', '; '.join('%s:%s' % kv for kv in cd.items()))

        # a span or link was an inline chip; a div was a container
        d['display'] = 'inline-block' if el.tag in INLINE_TAGS else 'block'
        el.set('style', '; '.join('%s:%s' % kv for kv in d.items()))


def _to_hls(c):
    import colorsys
    return colorsys.rgb_to_hls(*[v / 255.0 for v in c[:3]])


def _from_hls(h, l, s):
    import colorsys
    return tuple(int(round(v * 255)) for v in colorsys.hls_to_rgb(h, l, s)) + (1.0,)


def fix_contrast(colour, bg, need):
    """Nudge `colour` along its own hue until it reads against `bg`.

    Only the lightness moves, so a brand green stays a green - it just stops
    being a 12px green that nobody can read on white.
    """
    if _contrast(colour, bg) >= need:
        return None
    h, l, sat = _to_hls(colour)
    darker = _lum(bg) > 0.5          # light background -> darken the text
    for step in range(1, 101):
        nl = l - step * 0.01 if darker else l + step * 0.01
        if not 0.0 <= nl <= 1.0:
            break
        cand = _from_hls(h, nl, sat)
        if _contrast(cand, bg) >= need:
            return cand
    return (0, 0, 0, 1.0) if darker else (255, 255, 255, 1.0)


def readable(root, fixes):
    """Raise any text that would fail WCAG AA against its own background."""
    def walk(el, bg, colour, size, weight):
        d = dict(decls(el.get('style') or ''))
        if 'background-color' in d:
            c = parse_colour(d['background-color'])
            if c:
                bg = c
        if 'color' in d:
            c = parse_colour(d['color'])
            if c:
                colour = c
        m = re.match(r'^\s*([\d.]+)px', d.get('font-size', ''))
        if m:
            size = float(m.group(1))
        if d.get('font-weight', '').isdigit():
            weight = int(d['font-weight'])

        text = (el.text or '').strip()
        text += ''.join((c.tail or '') for c in el).strip()
        # one character counts: a tick or cross carries meaning and is
        # coloured by this very property
        if text and colour:
            need = 3.0 if (size >= 24 or (size >= 18.66 and weight >= 700)) else 4.5
            better = fix_contrast(colour, bg, need)
            if better and _contrast(better, bg) >= need:
                d['color'] = hexof(better)
                el.set('style', '; '.join('%s:%s' % kv for kv in d.items()))
                fixes.append((hexof(colour), hexof(better),
                              round(_contrast(colour, bg), 2), text[:34]))
                colour = better
            elif better and 'background-color' in d:
                # White text on a mid-tone button cannot be fixed by touching
                # the text - it is already at the extreme. Move the fill.
                darker = _lum(colour) > 0.5
                h, l, sat = _to_hls(bg)
                for step in range(1, 101):
                    nl = l - step * 0.01 if darker else l + step * 0.01
                    if not 0.0 <= nl <= 1.0:
                        break
                    cand = _from_hls(h, nl, sat)
                    if _contrast(colour, cand) >= need:
                        fixes.append((hexof(bg), hexof(cand) + ' (fill)',
                                      round(_contrast(colour, bg), 2), text[:34]))
                        d['background-color'] = hexof(cand)
                        el.set('style', '; '.join('%s:%s' % kv for kv in d.items()))
                        bg = cand
                        break
        for child in el:
            if isinstance(child.tag, str):
                walk(child, bg, colour, size, weight)

    walk(root, (255, 255, 255, 1.0), (51, 51, 51, 1.0), 16.0, 400)


def cap_image_widths(root, assets, capped):
    """Stop images being displayed above their natural width.

    A screenshot stretched past its own pixels just looks soft, and the store
    renders these at about 1000px. max-width is whitelisted, so the fix costs
    one declaration.
    """
    import os
    for img in root.xpath('.//img[@src]'):
        src = img.get('src').split('?')[0]
        path = os.path.join(assets, os.path.basename(src))
        if not os.path.exists(path):
            continue
        try:
            from PIL import Image
            with Image.open(path) as im:
                w = im.size[0]
        except Exception:
            continue
        d = dict(decls(img.get('style') or ''))
        if 'max-width' in d and d['max-width'].endswith('px'):
            continue
        d['max-width'] = '%dpx' % w
        d.setdefault('margin', '0 auto')
        d.setdefault('display', 'block')
        img.set('style', '; '.join('%s:%s' % kv for kv in d.items()))
        capped.append((os.path.basename(src), w))


def sanitizer_proof(html, assets=None):
    doc = LH.fragment_fromstring(html, create_parent='div')
    dropped = {}
    unflex(doc, dropped)

    def walk(el, bg):
        # top-down, so each element composites onto what it really sits on
        for child in el:
            if isinstance(child.tag, str):
                walk(child, proof_element(child, dropped, bg))

    walk(doc, (255, 255, 255, 1.0))

    capped = []
    if assets:
        cap_image_widths(doc, assets, capped)

    fixes = []
    readable(doc, fixes)
    inner = (doc.text or '') + ''.join(
        LH.tostring(c, encoding='unicode') for c in doc)
    return inner, dropped, fixes, capped


def audit(html):
    """Any property here would be dropped by the store, silently."""
    bad = {}
    for m in re.finditer(r'style="([^"]*)"|style=\'([^\']*)\'', html):
        for k, _ in decls(m.group(1) or m.group(2)):
            if k not in SAFE_PROPS:
                bad[k] = bad.get(k, 0) + 1
    return bad


def to_entities(html):
    """Escape every non-ASCII char so the file decodes identically anywhere.

    Numeric references only. Named HTML5 entities such as &check; are not
    understood by the store's parser, which escapes the ampersand instead and
    prints the entity as literal text - the tick marks published as the word
    "&check;". Numeric character references are universally understood.
    """
    return ''.join(c if ord(c) < 128 else '&#%d;' % ord(c) for c in html)


def process(path, assets=None):
    src = open(path, encoding='utf-8').read()
    styles = re.findall(r'<style[^>]*>(.*?)</style>', src, re.S)
    rep = {'path': path, 'inline': 0, 'media': 0, 'vars': 0,
           'pseudo_failed': [], 'unfolded': [], 'leftover': '',
           'dropped_props': {}, 'unsafe': {}, 'contrast_fixes': [], 'capped': []}
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
    html, rep['dropped_props'], rep['contrast_fixes'], rep['capped'] = \
        sanitizer_proof(html, assets)
    html = to_entities(html)
    rep['unsafe'] = audit(html)

    rep['inline'] = len(re.findall(r'style="', html))
    rep['stray_var'] = sorted(set(re.findall(r'var\(--[\w-]+', html)))
    rep['non_ascii'] = sum(1 for c in html if ord(c) > 127)
    open(path, 'w', encoding='utf-8').write(html + '\n')
    return rep


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    assets = next((a.split('=', 1)[1] for a in sys.argv[1:]
                   if a.startswith('--assets=')), None)
    for path in args:
        r = process(path, assets)
        drops = ', '.join('%s x%d' % kv for kv in
                          sorted(r['dropped_props'].items(), key=lambda x: -x[1])[:5])
        if drops:
            print("   converted/removed for the store: " + drops)
        if r['capped']:
            print("   capped to natural width: " + ', '.join(
                '%s@%dpx' % c for c in r['capped']))
        if r['contrast_fixes']:
            seen = {}
            for old, new, ratio_, _ in r['contrast_fixes']:
                seen.setdefault((old, new, ratio_), 0)
                seen[(old, new, ratio_)] += 1
            print("   contrast raised: " + ', '.join(
                '%s->%s (was %.2f:1) x%d' % (o, n, rr, c)
                for (o, n, rr), c in sorted(seen.items(), key=lambda x: -x[1])[:6]))
        print("%-24s inline=%-4d media=%-2d vars=%-3d non_ascii=%d"
              % (r['path'].split('/')[-3], r['inline'], r['media'],
                 r['vars'], r['non_ascii']))
        for label, val in (('LEFTOVER CSS', r['leftover']),
                           ('UNRESOLVED var()', ', '.join(r['stray_var'])),
                           ('BAD SELECTOR', ', '.join(r['pseudo_failed'])),
                           ('DROPPED @media', '; '.join(r['unfolded'])),
                           ('NOT WHITELISTED (store would strip)',
                            ', '.join('%s x%d' % kv for kv in sorted(r['unsafe'].items())))):
            if val:
                print("   !! %s: %s" % (label, val))
