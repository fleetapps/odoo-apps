"""Tiny stdlib-only PDF rewriter used by the mutation tests.

Crystal Reports writes simple PDFs: one FlateDecode content stream per page
with the figures as plain ``( 1,288.13) Tj`` strings and a classic xref table.
That is enough to make genuine one-character edits (or drop a page) and
write a valid PDF back, without adding a test dependency.
"""
import re
import zlib

_OBJ_RE = re.compile(rb"(?m)^(\d+) 0 obj\s*")
_STREAM_RE = re.compile(rb"stream\r?\n")


def _read_objects(data):
    objects = {}
    pos = 0
    while True:
        m = _OBJ_RE.search(data, pos)
        if not m:
            break
        num = int(m.group(1))
        body = m.end()
        end_obj = data.find(b"endobj", body)
        sm = _STREAM_RE.search(data, body)
        if sm and sm.start() < end_obj:
            head = data[body:sm.start()]
            length = int(re.search(rb"/Length (\d+)", head).group(1))
            raw = data[sm.end():sm.end() + length]
            end_obj = data.find(b"endobj", sm.end() + length)
            objects[num] = [head, raw]
        else:
            objects[num] = [data[body:end_obj], None]
        pos = end_obj + len(b"endobj")
    trailer = data[data.rfind(b"trailer"):data.rfind(b"startxref")]
    return objects, trailer


def _write(objects, trailer):
    out = bytearray(b"%PDF-1.2\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objects):
        head, raw = objects[num]
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num
        if raw is None:
            out += head.rstrip() + b"\nendobj\n"
        else:
            head = re.sub(rb"/Length \d+", b"/Length %d" % len(raw), head, count=1)
            out += head.rstrip() + b"\nstream\n" + raw + b"\nendstream\nendobj\n"
    size = max(objects) + 1
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for num in range(1, size):
        if num in offsets:
            out += b"%010d 00000 n \n" % offsets[num]
        else:
            out += b"0000000000 65535 f \n"
    trailer = re.sub(rb"/Size \d+", b"/Size %d" % size, trailer, count=1)
    out += trailer.rstrip() + b"\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def _page_content_ids(objects):
    pages = next(h for h, r in objects.values() if r is None and b"/Type /Pages" in h)
    kids = [int(k) for k in re.findall(rb"(\d+) 0 R", re.search(rb"/Kids \[(.*?)\]", pages, re.S).group(1))]
    content_ids = []
    for kid in kids:
        contents = re.search(rb"/Contents \[?\s*((?:\d+ 0 R\s*)+)\]?", objects[kid][0]).group(1)
        content_ids.append([int(c) for c in re.findall(rb"(\d+) 0 R", contents)])
    return kids, content_ids


def replace_text(data, old, new, occurrence=1, page=None):
    """Replace the ``occurrence``-th ``(old) Tj`` string (1-based, counted over
    the pages in order, or within ``page`` when given) by ``new``."""
    objects, trailer = _read_objects(data)
    _kids, content_ids = _page_content_ids(objects)
    needle = b"(" + old.encode() + b")"
    seen = 0
    for pno, ids in enumerate(content_ids, start=1):
        if page is not None and pno != page:
            continue
        for cid in ids:
            text = zlib.decompress(objects[cid][1])
            start = 0
            while True:
                i = text.find(needle, start)
                if i < 0:
                    break
                seen += 1
                if seen == occurrence:
                    text = text[:i] + b"(" + new.encode() + b")" + text[i + len(needle):]
                    objects[cid][1] = zlib.compress(text)
                    return _write(objects, trailer)
                start = i + len(needle)
    raise ValueError(f"{old!r} (occurrence {occurrence}) not found in the PDF text")


def drop_last_page(data):
    objects, trailer = _read_objects(data)
    kids, _ids = _page_content_ids(objects)
    num, head = next((n, h) for n, (h, r) in objects.items() if r is None and b"/Type /Pages" in h)
    kept = b" ".join(b"%d 0 R" % k for k in kids[:-1])
    head = re.sub(rb"/Kids \[.*?\]", b"/Kids [ " + kept + b" ]", head, count=1, flags=re.S)
    head = re.sub(rb"/Count \d+", b"/Count %d" % (len(kids) - 1), head, count=1)
    objects[num][0] = head
    return _write(objects, trailer)


def page_texts(data):
    """Decompressed content stream of each page (for tests that need to look)."""
    objects, _trailer = _read_objects(data)
    _kids, content_ids = _page_content_ids(objects)
    return [b"".join(zlib.decompress(objects[c][1]) for c in ids) for ids in content_ids]
