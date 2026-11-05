"""Read a personal M-PESA statement (the PDF Safaricom emails, or the same
table saved as CSV/XLSX) into one dict per transaction.

The statement lists every transaction with: Receipt No., Completion Time,
Details, Transaction Status, Paid In, Withdrawn, Balance. A charge is a row of
its own with the same receipt number ("... Charge"), so rows are grouped by
receipt: the main row gives the payee and amount, the charge rows add up to
the charge. Nothing here touches the database.
"""

import csv
import io
import re
from datetime import datetime

RECEIPT_RE = re.compile(r"^[A-Z0-9]{10}$")
AMOUNT_RE = r"-?[\d,]+\.\d{2}"
# One statement row as pypdf extracts it: receipt, time, details, status,
# then the amount (withdrawals are negative) and the balance.
PDF_ROW_RE = re.compile(
    r"^(?P<code>[A-Z0-9]{10})\s+(?P<time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<details>.*?)\s+(?P<status>Completed|Failed|Reversed|Pending)\s+"
    r"(?P<a1>" + AMOUNT_RE + r")(?:\s+(?P<a2>" + AMOUNT_RE + r"))?(?:\s+(?P<a3>" + AMOUNT_RE + r"))?\s*$"
)
# "Customer Transfer to 0723***614 - PAUL LUNYASI", "Pay Bill to 400200 - SHELL
# VIWANDANI Acc. KDU", "Merchant Payment to 123456 - NAIVAS", "Funds received
# from 0722***123 - JOHN DOE".
PARTY_RE = re.compile(
    r"\b(?:to|from)\s+(?:(?P<number>[\d*]{4,})\s*-\s*)?(?P<name>.+?)(?:\s+Acc\.?\s*(?P<acc>.*))?$",
    re.IGNORECASE,
)


def _amount(text):
    if text in (None, ""):
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    text = str(text).replace(",", "").strip()
    return float(text) if text not in ("", "-") else 0.0


def _time(value):
    if isinstance(value, datetime):
        return value
    value = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unreadable date: {value}")


def split_party(details):
    """Return (name, phone or till/paybill number, account) from the Details text."""
    match = PARTY_RE.search(details or "")
    if not match:
        return (details or "").strip(), "", ""
    number = match.group("number") or ""
    return match.group("name").strip(" -"), number, (match.group("acc") or "").strip()


def is_charge(details):
    return bool(re.search(r"\bcharge\b", details or "", re.IGNORECASE))


def _balance_after(rows):
    """The balance after all of a receipt's rows (payment and charge), whatever
    order the statement prints them in: the row that came last is the one
    whose balance no other row started from."""
    if len(rows) == 1:
        return rows[0][0]
    started_from = {round(balance - net, 2) for balance, net in rows}
    for balance, _net in rows:
        if round(balance, 2) not in started_from:
            return balance
    return rows[0][0]


def group_rows(rows):
    """Group raw rows ({code, time, details, status, paid_in, withdrawn, balance})
    into transactions. Failed and reversed rows are left out."""
    txs = {}
    order = []
    for row in rows:
        if row["status"] and row["status"].lower() not in ("completed",):
            continue
        tx = txs.get(row["code"])
        if tx is None:
            tx = txs[row["code"]] = {
                "code": row["code"], "time": row["time"], "details": "", "amount": 0.0,
                "charge": 0.0, "balance": None, "kind": None, "party": "", "number": "", "account": "",
            }
            order.append(row["code"])
        net = row["paid_in"] - row["withdrawn"]
        tx.setdefault("rows", []).append((row["balance"], net))
        if is_charge(row["details"]):
            tx["charge"] += -net if net < 0 else 0.0
        else:
            tx["details"] = row["details"]
            tx["amount"] = abs(net)
            tx["kind"] = "topup" if net > 0 else "payment"
            tx["party"], tx["number"], tx["account"] = split_party(row["details"])
    result = []
    for code in order:
        tx = txs[code]
        tx["balance"] = _balance_after(tx.pop("rows"))
        if tx["kind"] is None:  # a lone charge row: book it as a payment of its own
            tx.update(kind="payment", amount=tx["charge"], charge=0.0, details="M-PESA charge",
                      party="Safaricom")
        result.append(tx)
    result.sort(key=lambda t: t["time"])
    return result


def parse_pdf_text(text):
    """Rows from the text of a statement PDF. A Details cell that wraps onto
    the next line is joined back to its row."""
    rows = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        match = PDF_ROW_RE.match(line)
        if match:
            amounts = [match.group(k) for k in ("a1", "a2", "a3") if match.group(k)]
            balance = _amount(amounts[-1])
            moved = _amount(amounts[0]) if len(amounts) >= 2 else 0.0
            if len(amounts) == 3:  # Paid In and Withdrawn both printed (one is 0.00)
                moved = _amount(amounts[0]) or -abs(_amount(amounts[1]))
            rows.append({
                "code": match.group("code"), "time": _time(match.group("time")),
                "details": match.group("details"), "status": match.group("status"),
                "paid_in": moved if moved > 0 else 0.0, "withdrawn": -moved if moved < 0 else 0.0,
                "balance": balance,
            })
        elif rows and not RECEIPT_RE.match(line.split(" ")[0]) and not line.lower().startswith(
                ("receipt", "page", "disclaimer", "m-pesa", "mpesa", "customer", "statement", "detailed")):
            rows[-1]["details"] = f"{rows[-1]['details']} {line}".strip()
    return rows


def read_pdf(data, password=None):
    try:
        from pypdf import PdfReader
    except ImportError:  # Odoo 19 ships pypdf; older stacks have PyPDF2
        from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        if not password or not reader.decrypt(password):
            raise ValueError("This statement is password protected. Enter the password Safaricom sent "
                             "(usually the ID number) and try again.")
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return parse_pdf_text(text)


HEADERS = {
    "code": ("receipt no", "receipt no.", "receipt", "receipt number", "transaction id"),
    "time": ("completion time", "date", "transaction date", "time"),
    "details": ("details", "description"),
    "status": ("transaction status", "status"),
    "paid_in": ("paid in", "paid in (ksh)", "money in"),
    "withdrawn": ("withdrawn", "withdrawn (ksh)", "paid out", "money out"),
    "balance": ("balance", "balance (ksh)"),
}


def parse_table(table):
    """Rows from a list of lists (CSV or spreadsheet) with a header row anywhere
    near the top."""
    header_at, columns = None, {}
    for index, row in enumerate(table[:30]):
        names = [str(c or "").strip().lower() for c in row]
        found = {key: names.index(alias) for key, aliases in HEADERS.items()
                 for alias in aliases if alias in names}
        if {"code", "time", "details", "balance"} <= found.keys():
            header_at, columns = index, found
            break
    if header_at is None:
        raise ValueError("Could not find the statement columns (Receipt No., Completion Time, "
                         "Details, Paid In, Withdrawn, Balance).")
    rows = []
    for row in table[header_at + 1:]:
        get = lambda key: row[columns[key]] if key in columns and columns[key] < len(row) else None
        code = str(get("code") or "").strip()
        if not RECEIPT_RE.match(code):
            continue
        rows.append({
            "code": code, "time": _time(get("time")), "details": str(get("details") or "").strip(),
            "status": str(get("status") or "Completed").strip(),
            "paid_in": abs(_amount(get("paid_in"))), "withdrawn": abs(_amount(get("withdrawn"))),
            "balance": _amount(get("balance")),
        })
    return rows


def read_file(data, filename, password=None):
    """Transactions from an uploaded statement, whatever its format."""
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        rows = read_pdf(data, password)
    elif name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = parse_table([list(r) for r in book.active.iter_rows(values_only=True)])
    else:
        text = data.decode("utf-8-sig", errors="replace")
        rows = parse_table(list(csv.reader(io.StringIO(text))))
    if not rows:
        raise ValueError("No M-Pesa transactions were found in this file.")
    return group_rows(rows)


# Send-money tariff for registered users (KES), used only until the statement
# brings the real charge. Pay bill and till charges vary, so they wait for it.
SEND_TARIFF = [(100, 0), (500, 7), (1000, 13), (1500, 23), (2500, 33), (3500, 53), (5000, 57),
               (7500, 78), (10000, 90), (15000, 100), (20000, 105), (250000, 108)]


def send_money_charge(amount):
    for limit, charge in SEND_TARIFF:
        if amount <= limit:
            return float(charge)
    return 108.0
