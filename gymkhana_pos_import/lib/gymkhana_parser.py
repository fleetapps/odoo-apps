"""
Parser for the Nairobi Gymkhana "Group Sales Register - [Group, Sub Group And
Item Wise Sales Register [Summary]]" (Crystal Reports PDF).

Pure Python, no Odoo imports: it is unit-tested on its own and called from the
Odoo import. Depends only on pdfplumber.

``parse()`` returns a dict, or raises ``ParseError`` with a single plain-English
sentence when the document cannot be trusted. Every figure is cross-checked
against the report's own sub-group, group and grand totals (0.05 tolerance on
amount, discount and tax, which the report rounds column by column; zero
tolerance on net), so a mis-read line can never pass silently.
"""
import io
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0")
TOL = Decimal("0.05")

# The column header row, word by word, exactly as the report prints it.
HEADER_WORDS = ("Menu", "Item", "Unit", "Item", "Count", "Rate", "Quantity",
                "Amount", "Disc.", "Amt.", "Total", "Tax", "Net.", "Amt.")
# Numeric columns and the index (in HEADER_WORDS) of the word whose right edge
# the right-aligned figures line up with.
NUM_COLUMNS = (("count", 4), ("rate", 5), ("qty", 6), ("amount", 7),
               ("disc", 9), ("tax", 11), ("net", 13))
MONEY_COLUMNS = ("amount", "disc", "tax", "net")
DEFAULT_SUFFIXES = ("BB", "BE", "MB", "M")

NUM_RE = re.compile(r"^-?[\d,]+\.\d{2}$|^-?\d+$")
DATE_LINE_RE = re.compile(
    r"Date From:\s*(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})\s+"
    r"Outlet:\s*(.+?)(?:\s+Ignore Item Sales Rate)?\s*$")
MAX_COLUMN_DRIFT = 25    # pt between a figure's right edge and its header's
ROW_MERGE_GAP = 2        # pt: words whose tops differ by less are one row

_SUBGROUP_PHRASE = {
    "amount": "lines add up to",
    "disc": "line discounts add up to",
    "tax": "line tax adds up to",
    "net": "line net amounts add up to",
}
_COLUMN_LABEL = {"amount": "amount", "disc": "discount", "tax": "tax", "net": "net"}


class ParseError(Exception):
    """The PDF cannot be trusted; ``str(exc)`` is one plain-English sentence."""


def fmt(amount):
    """1234.5 -> '1,234.50' (the way the report prints money)."""
    return f"{Decimal(amount):,.2f}"


def make_key(name, unit):
    """Mapping key: case-, space- and bracket-spacing-insensitive name + unit.

    'Potato Crisps 100Gms ( Gita Foods)' and 'potato crisps 100gms (gita foods)'
    give the same key. Typos ('SAPRKLING') deliberately stay distinct: the user
    maps them once and the mapping remembers. The bar suffix is not part of the
    name passed here, so one mapping serves every bar.
    """
    n = re.sub(r"\(\s+", "(", name)
    n = re.sub(r"\s+\)", ")", n)
    n = re.sub(r"\s+", " ", n).strip().lower()
    u = re.sub(r"\s+", "", unit or "").lower()
    return f"{n}|{u}"


def split_suffix(name, suffixes=DEFAULT_SUFFIXES):
    """'Balozi Beer(BB)' -> ('Balozi Beer', 'BB'); no known suffix -> (name, None)."""
    alternatives = "|".join(re.escape(s) for s in sorted(suffixes, key=len, reverse=True))
    m = re.search(r"\s*\(\s*(%s)\s*\)\s*$" % alternatives, name, re.I)
    if not m:
        return name.strip(), None
    return name[:m.start()].strip(), m.group(1).upper()


def _dec(text):
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise ParseError(f"'{text}' is not a number.") from None


def _open(source):
    import pdfplumber  # imported late so make_key() & co. work without it

    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    try:
        return pdfplumber.open(source)
    except Exception:  # pdfminer raises a zoo of exception types
        raise ParseError("This file isn't a Gymkhana Group Sales Register: "
                         "it could not be opened as a PDF.") from None


def _rows(page):
    """Group words into visual rows by their 'top' coordinate."""
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    rows = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and w["top"] - rows[-1]["top"] < ROW_MERGE_GAP:
            rows[-1]["words"].append(w)
        else:
            rows.append({"top": w["top"], "words": [w]})
    return [sorted(r["words"], key=lambda w: w["x0"]) for r in rows]


def _find_header(rows):
    """Index of the column header row and the column geometry derived from it."""
    for idx, row in enumerate(rows):
        if tuple(w["text"] for w in row) == HEADER_WORDS:
            return idx, {
                "unit_x0": row[2]["x0"],
                # left edge of the "Item Count" heading: figures start right of it
                "num_x0": row[3]["x0"],
                "edges": {col: row[i]["x1"] for col, i in NUM_COLUMNS},
            }
    return None, None


def _assign_numbers(words, edges, pno, line):
    """Map right-aligned figures to the column whose right edge is nearest."""
    out = {}
    for w in words:
        if not NUM_RE.match(w["text"]):
            raise ParseError(f"Page {pno}: '{w['text']}' sits in a number column "
                             f"on the line '{line}'.")
        col = min(edges, key=lambda c: abs(edges[c] - w["x1"]))
        if abs(edges[col] - w["x1"]) > MAX_COLUMN_DRIFT:
            raise ParseError(f"Page {pno}: the figure '{w['text']}' on the line "
                             f"'{line}' doesn't line up with any column.")
        if col in out:
            raise ParseError(f"Page {pno}: two figures fall in the same column "
                             f"on the line '{line}'.")
        out[col] = _dec(w["text"])
    return out


def _require(values, cols, pno, line):
    missing = [c for c in cols if c not in values]
    if missing:
        raise ParseError(f"Page {pno}: the line '{line}' is missing "
                         f"{', '.join(_COLUMN_LABEL.get(c, c) for c in missing)}.")
    return values


def parse(source, suffixes=DEFAULT_SUFFIXES):
    """Parse a Group Sales Register PDF (path, bytes or file object)."""
    return interpret(extract(source), suffixes)


def extract(source):
    """Read the PDF into pages of visual rows (lists of pdfplumber words)."""
    with _open(source) as pdf:
        if not pdf.pages:
            raise ParseError("This file isn't a Gymkhana Group Sales Register: the PDF has no pages.")
        try:
            return [_rows(page) for page in pdf.pages]
        except Exception:
            raise ParseError("This file isn't a Gymkhana Group Sales Register: "
                             "its pages could not be read.") from None


def interpret(pages, suffixes=DEFAULT_SUFFIXES):
    """Turn the rows produced by ``extract()`` into checked sales lines."""
    header = None
    groups = []
    grand_total = None
    cur_group = cur_sub = last_line = last_heading = None
    seq = 0
    page_count = len(pages)

    for pno, rows in enumerate(pages, start=1):
        if not rows:
            raise ParseError(f"Page {pno} is blank.")
        hidx, geo = _find_header(rows)
        if hidx is None:
            if pno == 1:
                raise ParseError("This file isn't a Gymkhana Group Sales Register: "
                                 "the column header row was not found.")
            raise ParseError(f"Page {pno} has no column header row: the layout of "
                             f"the report has changed.")

        # ---- page furniture above the column header -------------------
        page_header = None
        for row in rows[:hidx]:
            line = " ".join(w["text"] for w in row)
            m = DATE_LINE_RE.search(line)
            if m:
                try:
                    page_header = {
                        "date_from": datetime.strptime(m.group(1), "%m/%d/%Y").date(),
                        "date_to": datetime.strptime(m.group(2), "%m/%d/%Y").date(),
                        "outlet": m.group(3).strip(),
                    }
                except ValueError:
                    raise ParseError(f"Page {pno}: the date range '{m.group(1)} to "
                                     f"{m.group(2)}' is not a valid date.") from None
        if page_header is None:
            raise ParseError(f"Page {pno}: the 'Date From ... Outlet' line was not found.")
        if header is None:
            header = page_header
        elif page_header != header:
            raise ParseError(f"Page {pno} is for a different date range or outlet than page 1.")

        unit_x0, num_x0, edges = geo["unit_x0"], geo["num_x0"], geo["edges"]

        # ---- report body ------------------------------------------------
        for row in rows[hidx + 1:]:
            texts = [w["text"] for w in row]
            line = " ".join(texts)
            nums = [w for w in row if w["x0"] >= num_x0 - 2]
            if grand_total is not None:
                raise ParseError(f"Page {pno}: unexpected line after the Grand Total: '{line}'.")

            # structure rows are recognised by their leading words
            if texts[:3] == ["Sub", "Group", "Total:"]:
                if cur_sub is None or cur_sub["total"] is not None:
                    raise ParseError(f"Page {pno}: a Sub Group Total without its sub group.")
                values = _assign_numbers([w for w in row[3:]], edges, pno, line)
                cur_sub["total"] = _require(values, MONEY_COLUMNS, pno, line)
                last_line = last_heading = None
                continue
            if texts[:2] == ["Group", "Total:"]:
                if cur_group is None or cur_group["total"] is not None:
                    raise ParseError(f"Page {pno}: a Group Total without its group.")
                values = _assign_numbers([w for w in row[2:]], edges, pno, line)
                cur_group["total"] = _require(values, MONEY_COLUMNS, pno, line)
                cur_sub = last_line = last_heading = None
                continue
            if texts[:2] == ["Grand", "Total:"]:
                values = _assign_numbers([w for w in row[2:]], edges, pno, line)
                grand_total = _require(values, MONEY_COLUMNS, pno, line)
                cur_group = cur_sub = last_line = last_heading = None
                continue
            has_figures = any(NUM_RE.match(w["text"]) for w in nums)
            if texts[:2] == ["Sub", "Group"] and len(texts) > 2 and not has_figures:
                if cur_group is None:
                    raise ParseError(f"Page {pno}: sub group '{' '.join(texts[2:])}' "
                                     f"appears before any group.")
                cur_sub = {"name": " ".join(texts[2:]), "lines": [], "total": None}
                cur_group["subgroups"].append(cur_sub)
                last_line = None
                last_heading = cur_sub
                continue
            if texts[0] == "Group" and len(texts) > 1 and not has_figures:
                cur_group = {"name": " ".join(texts[1:]), "subgroups": [], "total": None}
                groups.append(cur_group)
                cur_sub = last_line = None
                last_heading = cur_group
                continue
            if last_heading is not None and not has_figures:
                # a long group / sub group name wraps onto the next visual row
                last_heading["name"] += " " + line
                continue
            last_heading = None

            # item rows and wrapped item names
            name_words = [w["text"] for w in row if w["x0"] < unit_x0 - 2]
            unit_words = [w["text"] for w in row if unit_x0 - 2 <= w["x0"] < num_x0 - 2]
            if nums:
                if cur_sub is None:
                    raise ParseError(f"Page {pno}: the item line '{line}' is outside a sub group.")
                values = _require(_assign_numbers(nums, edges, pno, line),
                                  [c for c, _i in NUM_COLUMNS], pno, line)
                if not name_words:
                    raise ParseError(f"Page {pno}: figures without an item name: '{line}'.")
                seq += 1
                last_line = {"raw_name": " ".join(name_words), "unit": " ".join(unit_words),
                             "page": pno, "sequence": seq, **values}
                cur_sub["lines"].append(last_line)
                continue
            if last_line is not None and (name_words or unit_words):
                # a long item name (or unit) wraps onto the next visual row
                if name_words:
                    last_line["raw_name"] += " " + " ".join(name_words)
                if unit_words:
                    last_line["unit"] = (last_line["unit"] + " " + " ".join(unit_words)).strip()
                continue
            raise ParseError(f"Page {pno}: couldn't recognise the line '{line}'.")

    if not groups:
        raise ParseError("The report contains no sales groups.")
    if grand_total is None:
        raise ParseError("Grand Total missing: the PDF is probably cut off "
                         "(is the last page missing?).")

    lines = _finalise(groups, suffixes)
    _check_totals(groups, grand_total)
    return {
        "header": header,
        "groups": groups,
        "grand_total": grand_total,
        "lines": lines,
        "page_count": page_count,
    }


def _finalise(groups, suffixes):
    flat = []
    for g in groups:
        if not g["subgroups"]:
            raise ParseError(f"Group {g['name']} has no sub groups.")
        for s in g["subgroups"]:
            s["base_name"], s["bar_suffix"] = split_suffix(s["name"], suffixes)
            if not s["lines"]:
                raise ParseError(f"Sub group {s['name']} has no item lines.")
            for ln in s["lines"]:
                name = re.sub(r"\s+", " ", ln["raw_name"]).strip()
                ln["raw_name"] = name
                ln["name"], ln["bar_suffix"] = split_suffix(name, suffixes)
                ln["key"] = make_key(ln["name"], ln["unit"])
                ln["group"] = g["name"]
                ln["sub_group"] = s["name"]
                if ln["qty"] <= 0:
                    raise ParseError(f"Page {ln['page']}: {name} has a quantity of "
                                     f"{ln['qty']}; refunds and voids can't be imported.")
                if ln["net"] < 0:
                    raise ParseError(f"Page {ln['page']}: {name} has a negative net "
                                     f"amount ({fmt(ln['net'])}).")
                # internal consistency of the line itself
                diff = ln["amount"] - ln["disc"] + ln["tax"] - ln["net"]
                if abs(diff) > TOL:
                    raise ParseError(
                        f"Page {ln['page']}: {name}: amount {fmt(ln['amount'])} less discount "
                        f"{fmt(ln['disc'])} plus tax {fmt(ln['tax'])} doesn't give the net "
                        f"{fmt(ln['net'])}.")
                # tax-included unit price: exact when Net divides by Qty to the cent
                ln["price_unit"] = (ln["net"] / ln["qty"]).quantize(CENT, ROUND_HALF_UP)
                ln["price_exact"] = ln["price_unit"] * ln["qty"] == ln["net"]
                # Quantity has no total in the report, so cross-check it against the
                # (rounded) rate: rate x qty must give the amount before discount,
                # give or take half a cent per unit. Not blocking: a warning.
                slack = CENT + Decimal("0.005") * ln["qty"]
                ln["rate_mismatch"] = abs(ln["rate"] * ln["qty"] - ln["amount"]) > slack
                flat.append(ln)
    return flat


def _check_totals(groups, grand_total):
    def tolerance(col):
        return ZERO if col == "net" else TOL

    gsum = defaultdict(Decimal)
    for g in groups:
        if g["total"] is None:
            raise ParseError(f"Group {g['name']} has no Group Total line.")
        grp = defaultdict(Decimal)
        for s in g["subgroups"]:
            if s["total"] is None:
                raise ParseError(f"Sub group {s['name']} has no Sub Group Total line.")
            for col in MONEY_COLUMNS:
                ssum = sum((ln[col] for ln in s["lines"]), ZERO)
                if abs(ssum - s["total"][col]) > tolerance(col):
                    raise ParseError(f"Sub group {s['name']}: {_SUBGROUP_PHRASE[col]} "
                                     f"{fmt(ssum)} but the report says {fmt(s['total'][col])}.")
                grp[col] += s["total"][col]
        for col in MONEY_COLUMNS:
            if abs(grp[col] - g["total"][col]) > tolerance(col):
                raise ParseError(f"Group {g['name']}: sub group {_COLUMN_LABEL[col]} totals add up "
                                 f"to {fmt(grp[col])} but the report says {fmt(g['total'][col])}.")
            gsum[col] += g["total"][col]
    for col in MONEY_COLUMNS:
        if abs(gsum[col] - grand_total[col]) > tolerance(col):
            raise ParseError(f"Grand Total: group {_COLUMN_LABEL[col]} totals add up to "
                             f"{fmt(gsum[col])} but the report says {fmt(grand_total[col])}.")
    # Net must match to the cent end to end: it is the money the club owes.
    net_lines = sum((ln["net"] for g in groups for s in g["subgroups"] for ln in s["lines"]), ZERO)
    if net_lines != grand_total["net"]:
        raise ParseError(f"Item net amounts add up to {fmt(net_lines)} but the Grand Total "
                         f"says {fmt(grand_total['net'])}.")


if __name__ == "__main__":
    import sys

    res = parse(sys.argv[1])
    print(res["header"])
    for g in res["groups"]:
        glines = [ln for s in g["subgroups"] for ln in s["lines"]]
        print(f"{g['name']}: {len(glines)} lines, net {fmt(g['total']['net'])}")
        for ln in glines:
            flag = "" if ln["price_exact"] else "  (net not divisible by qty)"
            print(f"   {ln['name']:<38} {ln['unit']:<7} {ln['bar_suffix'] or '-':<3} "
                  f"qty {ln['qty']:>6} @ {ln['price_unit']:>9} = {fmt(ln['net']):>10}{flag}")
    print(f"lines: {len(res['lines'])}, grand net: {fmt(res['grand_total']['net'])}, "
          f"tax: {fmt(res['grand_total']['tax'])}")
