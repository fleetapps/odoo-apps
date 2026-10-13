"""
Parser for the Nairobi Gymkhana "Group Sales Register - [Group, Sub Group And
Item Wise Sales Register [Summary]]" (Crystal Reports).

The same report comes out of the POS as a PDF or as a spreadsheet, and both
are accepted: ``parse()`` looks at the file itself to tell which it is. A
spreadsheet is laid out onto a synthetic page so that it arrives at the
reading and checking step in exactly the shape a PDF page does -- one code
path interprets both, so neither can drift away from the other's checks.

Pure Python, no Odoo imports: it is unit-tested on its own and called from the
Odoo import. Needs pdfplumber for PDFs, xlrd for .xls and openpyxl for .xlsx,
each imported only when a file of that kind turns up.

``parse()`` returns a dict, or raises ``ParseError`` with a single plain-English
sentence when the document cannot be trusted. Every figure is cross-checked
against the report's own sub-group, group and grand totals (0.05 tolerance on
amount, discount and tax, which the report rounds column by column; zero
tolerance on net), so a mis-read line can never pass silently.
"""
import io
import math
import re
from collections import defaultdict
from datetime import date, datetime
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
# "Printed Date : 09/28/2026 10:52:34" -- a date followed by a time, which the
# period line above never is, so the two cannot be confused for one another.
PRINTED_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\s+\d{1,2}:\d{2}(?::\d{2})?\b")

# The report writes its period one way in the PDF and the other way in the
# spreadsheet: 09/27/2026 is 27 September in the PDF, 01/10/2026 is 1 October
# in the sheet. Nothing in a single file distinguishes 01/10 from 10/01, so
# the format is fixed per kind of export rather than guessed per file, and
# the printed-on stamp is used to catch an export that changes its mind.
PDF_DATE_FORMAT = "%m/%d/%Y"
SHEET_DATE_FORMAT = "%d/%m/%Y"

PDF_MAGIC = b"%PDF"
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .xls workbook
ZIP_MAGIC = b"PK\x03\x04"  # .xlsx, and every other zip
# Synthetic width of one spreadsheet column, in the points a PDF is measured
# in. Only the ratios matter: it has to be comfortably more than the drift a
# figure is allowed from its heading, so neighbouring columns stay distinct.
CELL_WIDTH = 100.0
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


# ----------------------------------------------------------------------------
# Which kind of file this is, and reading a spreadsheet like a page
# ----------------------------------------------------------------------------


def _peek(source, size=8):
    """The first bytes of ``source`` (path, bytes or file object), leaving a
    file object where it was found."""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source[:size])
    if hasattr(source, "read"):
        position = source.tell()
        head = source.read(size)
        source.seek(position)
        return head
    with open(source, "rb") as fh:
        return fh.read(size)


def _read_bytes(source):
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "read"):
        position = source.tell()
        data = source.read()
        source.seek(position)
        return data
    with open(source, "rb") as fh:
        return fh.read()


def sniff(source):
    """'pdf', 'xls' or 'xlsx', from the file's own first bytes.

    The extension is not consulted: the POS has been seen to hand out a file
    named .xls that is really something else, and the user should be told what
    is wrong with the file rather than what it is called.
    """
    head = _peek(source)
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(OLE2_MAGIC):
        return "xls"
    if head.startswith(ZIP_MAGIC):
        return "xlsx"
    if head.lstrip()[:1] == b"<":
        raise ParseError("This file is a web page, not a Group Sales Register: "
                         "export it from the POS as PDF, Excel or Excel 97.")
    raise ParseError("This file isn't a Gymkhana Group Sales Register: it is "
                     "not a PDF, an .xls or an .xlsx file.")


def _cell_text(value):
    """A spreadsheet cell as the report prints it.

    Figures arrive from a sheet as numbers, so 760.0 has to be written '760'
    and 4995.8 '4995.80' -- the two shapes a figure is allowed to take -- or a
    total the report shows plainly would be read as not a number at all.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, date):  # datetime too: it is a date
        return value.strftime(SHEET_DATE_FORMAT)
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return str(int(value)) if value == int(value) else f"{value:.2f}"
    return str(value).strip()


def _row_words(top, cells):
    """Lay one sheet row out on a synthetic page as PDF-shaped words.

    Each cell takes its column's slice of the page and the words inside it
    share that slice, so the last word of a cell always ends exactly on the
    column's right edge. That is what makes a figure line up with its heading:
    'Net. Amt.' ends on the right edge of its column, and so does every net
    figure underneath it.
    """
    words = []
    for index, text in cells:
        parts = text.split()
        if not parts:
            continue
        left = index * CELL_WIDTH
        width = CELL_WIDTH / len(parts)
        for position, part in enumerate(parts):
            x0 = left + position * width
            words.append({"text": part, "x0": x0, "x1": x0 + width, "top": float(top)})
    return words


def _xls_rows(source):
    try:
        import xlrd
    except ImportError:
        raise ParseError("This server cannot read .xls files: the xlrd library "
                         "is not installed. Export the report as PDF or .xlsx.") from None
    try:
        book = xlrd.open_workbook(file_contents=_read_bytes(source))
        sheet = book.sheet_by_index(0)
    except Exception:
        raise ParseError("This file isn't a Gymkhana Group Sales Register: it "
                         "could not be opened as an Excel 97 workbook.") from None
    rows = []
    for r in range(sheet.nrows):
        cells = []
        for c in range(sheet.ncols):
            if sheet.cell_type(r, c) == xlrd.XL_CELL_DATE:
                value = xlrd.xldate_as_datetime(sheet.cell_value(r, c), book.datemode)
            else:
                value = sheet.cell_value(r, c)
            text = _cell_text(value)
            if text:
                cells.append((c, text))
        rows.append(cells)
    return rows


def _xlsx_rows(source):
    try:
        import openpyxl
    except ImportError:
        raise ParseError("This server cannot read .xlsx files: the openpyxl "
                         "library is not installed. Export the report as a PDF.") from None
    try:
        book = openpyxl.load_workbook(io.BytesIO(_read_bytes(source)),
                                      read_only=True, data_only=True)
    except Exception:
        raise ParseError("This file isn't a Gymkhana Group Sales Register: it "
                         "could not be opened as an Excel workbook.") from None
    try:
        sheet = book.worksheets[0]
        rows = []
        for raw in sheet.iter_rows(values_only=True):
            cells = []
            for index, value in enumerate(raw):
                text = _cell_text(value)
                if text:
                    cells.append((index, text))
            rows.append(cells)
        return rows
    except Exception:
        raise ParseError("This file isn't a Gymkhana Group Sales Register: its "
                         "first sheet could not be read.") from None
    finally:
        book.close()


def extract_sheet(source, kind):
    """Read a spreadsheet into one page of visual rows.

    The export is one continuous sheet -- the page furniture and the column
    headings appear once, not once per printed page -- so it is read as a
    single page however many pages it would print on.
    """
    rows = _xls_rows(source) if kind == "xls" else _xlsx_rows(source)
    page = [words for words in (_row_words(top, cells) for top, cells in enumerate(rows)) if words]
    if not page:
        raise ParseError("This file isn't a Gymkhana Group Sales Register: the sheet is empty.")
    return [page]


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
    """Parse a Group Sales Register (path, bytes or file object).

    PDF or spreadsheet, told apart by the file itself; both end up in
    ``interpret()`` and face exactly the same checks.
    """
    kind = sniff(source)
    if kind == "pdf":
        return interpret(extract(source), suffixes, PDF_DATE_FORMAT)
    return interpret(extract_sheet(source, kind), suffixes, SHEET_DATE_FORMAT)


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


def interpret(pages, suffixes=DEFAULT_SUFFIXES, date_format=PDF_DATE_FORMAT):
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
        printed_on = None
        for row in rows[:hidx]:
            line = " ".join(w["text"] for w in row)
            stamp = PRINTED_RE.search(line)
            if stamp and printed_on is None:
                try:
                    printed_on = datetime.strptime(stamp.group(1), date_format).date()
                except ValueError:
                    printed_on = None
            m = DATE_LINE_RE.search(line)
            if m:
                try:
                    page_header = {
                        "date_from": datetime.strptime(m.group(1), date_format).date(),
                        "date_to": datetime.strptime(m.group(2), date_format).date(),
                        "outlet": m.group(3).strip(),
                    }
                except ValueError:
                    raise ParseError(f"Page {pno}: the date range '{m.group(1)} to "
                                     f"{m.group(2)}' is not a valid date.") from None
        if page_header is None:
            raise ParseError(f"Page {pno}: the 'Date From ... Outlet' line was not found.")
        # Two things the report cannot truthfully say. Both are what a period
        # read back to front looks like -- 01/10 to 31/10 read month-first
        # gives an impossible month, and 02/10 printed for 01/10 read the
        # wrong way round lands a month out -- so if the POS ever changes how
        # it writes dates, the import stops instead of booking the wrong day.
        if page_header["date_from"] > page_header["date_to"]:
            raise ParseError(f"Page {pno}: the report covers {page_header['date_from']} "
                             f"to {page_header['date_to']}, which ends before it starts.")
        if printed_on is not None and printed_on < page_header["date_to"]:
            raise ParseError(f"Page {pno}: the report covers up to {page_header['date_to']} "
                             f"but says it was printed on {printed_on}, before the day it "
                             f"reports. Check the date format of the POS export.")
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
