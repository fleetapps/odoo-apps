# -*- coding: utf-8 -*-
"""Reconciling the ledger against the return KRA has already written.

Since March 2024 KRA pre-fills the VAT return from eTIMS, TIMS and customs, and
in August 2026 filing moved to direct web entry on iTax. The pre-filled lines
cannot be edited: on sales, KRA locks the purchaser PIN, the invoice number, the
invoice date and the taxable amount; on purchases, none of those may be amended
and the taxable value may not be adjusted upward.

So the question on the 19th of the month is not "what are our numbers". It is
"why do ours differ from KRA's, and which invoices account for the difference".
That is a reconciliation, and it is what this file does.

A row here is a match candidate, in the shape of a bank reconciliation rather
than two facing reports. A row with KRA data and no Odoo document is something
KRA has that we do not; a row with an Odoo document and no KRA data is something
we have that KRA has not received. Keeping both in one model means one list, one
set of filters, and no way to look at one side while forgetting the other.

Three KRA rules are modelled here because getting them wrong costs money:

* **Input tax is claimable for six months** after the end of the period in which
  the supply occurred. Input not claimed this month is a position to track
  against a deadline, not a number to write off.
* **Suppliers on the VAT special table** cannot have their invoices claimed by
  their customers, and a return carrying one is rejected at filing. The
  restriction is relaxed for credit notes.
* **Untransmitted sales must still be declared.** Pre-population runs on invoice
  date, not transmission date, and the obligation stands regardless. So the
  reconciliation has to produce the lumpsum figure, not merely flag the gap.
"""

import base64
import csv
import io
import logging
from datetime import datetime
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# How many rows we will take from one CSV. KRA files are per-period and per
# section, so this is generous; it exists so a wrong file pasted into the wrong
# field fails with a message instead of eating the worker.
MAX_CSV_ROWS = 100000

# What each section is, which Odoo documents it faces, and whether input tax
# rules apply to it. KRA names its downloads by section; these are ours.
#
# ``domain`` narrows the documents a section faces beyond their move type.
# Several sections share a move type -- both sales sections face customer
# invoices, three purchase sections face vendor bills -- and without it each
# would count every document that belongs to a sibling section as "in Odoo,
# not in KRA". The sales split is the one KRA itself makes, on whether the
# customer has a PIN. Purchases split on the supplier being Kenyan: a foreign
# supplier never appears in KRA's local-purchases feed, and a local one never
# appears in customs data.
#
# Order matters. A document KRA has not reported is listed once, under the
# first section in this order whose domain it satisfies, so a foreign
# supplier's bill lands under customs imports and not a second time under
# digital services. ``lumpsum`` marks the sections whose unreported documents
# make up the lumpsum declaration: sales KRA has not received. Credit notes
# and purchases never add to it.
LOCAL_SUPPLIER_DOMAIN = [
    "|", ("commercial_partner_id.country_id", "=", False),
    ("commercial_partner_id.country_id.code", "=", "KE"),
]
FOREIGN_SUPPLIER_DOMAIN = [
    ("commercial_partner_id.country_id", "!=", False),
    ("commercial_partner_id.country_id.code", "!=", "KE"),
]
SECTIONS = {
    "sales_pin": {
        "label": "Sales to VAT-registered customers",
        "move_types": ("out_invoice",),
        "side": "sale",
        "domain": [("commercial_partner_id.vat", "!=", False)],
        "lumpsum": True,
    },
    "sales_no_pin": {
        "label": "Sales to customers without a PIN",
        "move_types": ("out_invoice",),
        "side": "sale",
        "domain": [("commercial_partner_id.vat", "=", False)],
        "lumpsum": True,
    },
    "sales_credit_notes": {
        "label": "Credit notes on sales",
        "move_types": ("out_refund",),
        "side": "sale",
        "domain": [],
        "lumpsum": False,
    },
    "purchases_local": {
        "label": "Local purchases",
        "move_types": ("in_invoice",),
        "side": "purchase",
        "domain": LOCAL_SUPPLIER_DOMAIN,
        "lumpsum": False,
    },
    "purchases_credit_notes": {
        "label": "Credit notes on purchases",
        "move_types": ("in_refund",),
        "side": "purchase",
        "domain": [],
        "lumpsum": False,
    },
    "purchases_imports": {
        "label": "Customs imports",
        "move_types": ("in_invoice",),
        "side": "purchase",
        "domain": FOREIGN_SUPPLIER_DOMAIN,
        "lumpsum": False,
    },
    "purchases_dst": {
        "label": "Digital service tax purchases",
        "move_types": ("in_invoice",),
        "side": "purchase",
        "domain": FOREIGN_SUPPLIER_DOMAIN,
        "lumpsum": False,
    },
}
SECTION_ORDER = list(SECTIONS)

# KRA has renamed these columns between releases and localises some of them, so
# headers are matched by alias rather than position. Comparison is on the
# lower-cased header with spaces and underscores removed.
COLUMN_ALIASES = {
    "kra_pin": ("pinofpurchaser", "pinofsupplier", "pin", "supplierpin",
                "customerpin", "traderpin"),
    "kra_partner_name": ("nameofpurchaser", "nameofsupplier", "name",
                         "suppliername", "customername", "tradername"),
    "kra_invoice_no": ("invoiceno", "invoicenumber", "etimsinvoiceno",
                       "taxinvoiceno", "invno"),
    "kra_invoice_date": ("invoicedate", "dateofinvoice", "invdate", "date"),
    "kra_taxable_amount": ("taxablevalue", "taxableamount", "amountexcltax",
                           "taxablevalueksh"),
    "kra_tax_amount": ("amountofvat", "vatamount", "taxamount", "vat"),
    "kra_entry_no": ("customsentryno", "entryno", "customsentrynumber"),
}


def _normalise_header(header):
    return (header or "").strip().lower().replace(" ", "").replace("_", "").replace(".", "")


def _normalise_pin(value):
    return (value or "").strip().upper()


def _normalise_number(value):
    return (value or "").strip().upper()


class KeVatItaxBatch(models.Model):
    _name = "ke.vat.itax.batch"
    _description = "KRA iTax CSV Import"
    _order = "return_id desc, section"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    return_id = fields.Many2one(
        "ke.vat.return", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(
        related="return_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="return_id.currency_id")
    section = fields.Selection(
        [(key, spec["label"]) for key, spec in SECTIONS.items()],
        required=True)

    csv_file = fields.Binary(string="KRA CSV", attachment=True)
    csv_filename = fields.Char()
    date_pulled = fields.Date(
        string="Downloaded On", default=fields.Date.context_today,
        help="When this CSV was downloaded from iTax. KRA loads invoices into "
             "the auto-populated CSVs in batches, so a file pulled early in the "
             "month legitimately understates. Re-download and re-import.")

    line_ids = fields.One2many("ke.vat.itax.line", "batch_id")
    line_count = fields.Integer(compute="_compute_counts")
    matched_count = fields.Integer(compute="_compute_counts")
    exception_count = fields.Integer(compute="_compute_counts")
    lumpsum_amount = fields.Monetary(
        compute="_compute_counts",
        help="Sales we have posted that KRA has not received. The law still "
             "requires them to be declared, through the lumpsum field on the "
             "return. Only the two sales sections carry a figure; credit "
             "notes and purchases are never part of the lumpsum.")

    _section_uniq = models.Constraint(
        "UNIQUE(return_id, section)",
        "That section has already been imported for this return. Upload the "
        "newer CSV to the existing import instead of creating a second one.")

    @api.depends("return_id", "section")
    def _compute_name(self):
        for record in self:
            label = dict(record._fields["section"].selection).get(record.section, "")
            record.name = "%s - %s" % (record.return_id.display_name or "", label)

    @api.depends("line_ids.match_state", "line_ids.odoo_taxable_amount")
    def _compute_counts(self):
        for record in self:
            lines = record.line_ids
            record.line_count = len(lines)
            record.matched_count = len(
                lines.filtered(lambda line: line.match_state == "matched"))
            record.exception_count = len(
                lines.filtered(lambda line: line.match_state != "matched"))
            record.lumpsum_amount = sum(
                lines.filtered(lambda line: line.match_state == "odoo_only")
                .mapped("odoo_taxable_amount")
            ) if SECTIONS.get(record.section, {}).get("lumpsum") else 0.0

    # -------------------------------------------------------------- import

    def action_import(self):
        """Converge this section's rows on what the CSV currently holds.

        KRA adds to these files over the course of the month, so the same
        section gets downloaded repeatedly, and every import has to converge
        on the file rather than accumulate copies of it. It converges by
        upsert, keyed on the KRA invoice number (and PIN, where the file has
        one): a row already known is updated in place, a row no longer in the
        file is removed, a new row is created. Updating in place is what keeps
        the accountant's decisions -- a deferred claim, a note -- across the
        re-imports the workflow requires. A wholesale replace would throw them
        away every time a fuller file arrived.
        """
        Line = self.env["ke.vat.itax.line"]
        for record in self:
            if not record.csv_file:
                raise UserError(_(
                    "Attach the CSV downloaded from iTax before importing."))
            rows = record._parse_csv()
            existing = {
                line._kra_key(): line
                for line in record.line_ids.filtered(lambda line: line.kra_invoice_no)
            }
            pending, seen = {}, set()
            for row in rows:
                key = (row["kra_invoice_no"], row["kra_pin"])
                seen.add(key)
                if key in existing:
                    # Known row: KRA's figures may have changed, ours stay.
                    existing[key].write(row)
                elif key in pending:
                    # KRA files can repeat a row; the last occurrence wins.
                    pending[key].update(row)
                else:
                    pending[key] = dict(row, batch_id=record.id)
            stale = Line.concat(*(
                line for key, line in existing.items() if key not in seen))
            stale.unlink()
            if pending:
                Line.create(list(pending.values()))
            _logger.info(
                "x_ke_vat: imported %d row(s) into %s (%d new, %d removed)",
                len(rows), record.display_name, len(pending), len(stale))
            record.action_match()
        return True

    def _parse_csv(self):
        """Read the CSV into line values, tolerating KRA's column renames."""
        self.ensure_one()
        raw = base64.b64decode(self.csv_file)
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise UserError(_(
                "%(file)s is not readable as text. Download it again from "
                "iTax as CSV.", file=self.csv_filename or _("The file")))

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise UserError(_("%(file)s has no header row.",
                              file=self.csv_filename or _("The file")))

        mapping = {}
        for header in reader.fieldnames:
            normalised = _normalise_header(header)
            for field_name, aliases in COLUMN_ALIASES.items():
                if normalised in aliases and field_name not in mapping:
                    mapping[field_name] = header

        if "kra_invoice_no" not in mapping:
            raise UserError(_(
                "No invoice-number column found in %(file)s. Its headers are: "
                "%(headers)s",
                file=self.csv_filename or _("the file"),
                headers=", ".join(reader.fieldnames)))

        rows = []
        for count, raw_row in enumerate(reader, start=1):
            if count > MAX_CSV_ROWS:
                raise UserError(_(
                    "%(file)s has more than %(limit)s rows, which is far more "
                    "than a VAT period holds. Check it is the right file.",
                    file=self.csv_filename or _("The file"), limit=MAX_CSV_ROWS))
            invoice_no = _normalise_number(raw_row.get(mapping["kra_invoice_no"]))
            if not invoice_no:
                continue
            rows.append({
                "kra_invoice_no": invoice_no,
                "kra_pin": _normalise_pin(
                    raw_row.get(mapping.get("kra_pin", ""), "")),
                "kra_partner_name": (
                    raw_row.get(mapping.get("kra_partner_name", ""), "") or "").strip(),
                "kra_invoice_date": self._parse_date(
                    raw_row.get(mapping.get("kra_invoice_date", ""), "")),
                "kra_taxable_amount": self._parse_amount(
                    raw_row.get(mapping.get("kra_taxable_amount", ""), "")),
                "kra_tax_amount": self._parse_amount(
                    raw_row.get(mapping.get("kra_tax_amount", ""), "")),
                "kra_entry_no": (
                    raw_row.get(mapping.get("kra_entry_no", ""), "") or "").strip(),
            })
        return rows

    @api.model
    def _parse_date(self, value):
        value = (value or "").strip()
        if not value:
            return False
        for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%Y%m%d"):
            try:
                return datetime.strptime(value, pattern).date()
            except ValueError:
                continue
        return False

    @api.model
    def _parse_amount(self, value):
        value = (value or "").strip().replace(",", "")
        if not value:
            return 0.0
        try:
            return float(value)
        except ValueError:
            return 0.0

    # --------------------------------------------------------------- match

    def action_match(self):
        """Classify every row against the ledger, both directions.

        KRA rows are matched per batch. "In Odoo, not in KRA" is worked out
        per return, across every section imported for it, so that a document
        one section has matched is never reported missing by another.
        """
        for record in self:
            record._match_kra_rows()
        self.return_id._ke_refresh_odoo_only()
        return True

    def _candidate_moves(self):
        """The Odoo documents this section faces, for this period.

        Selected on ``date``, the accounting date, because that is what the
        return itself sums: the renderer aggregates ``account.move.line.date``.
        Picking candidates on ``invoice_date`` instead would let the two halves
        cover different sets of invoices whenever the document date and the
        accounting date differ, and they would disagree while appearing to
        reconcile.

        KRA pre-populates by invoice date, so a move whose two dates straddle a
        month end legitimately shows up as an exception. That is a finding, not
        a defect: it is precisely the kind of cut-off difference this screen
        exists to surface.
        """
        self.ensure_one()
        spec = SECTIONS[self.section]
        return self.env["account.move"].search([
            ("company_id", "=", self.company_id.id),
            ("move_type", "in", list(spec["move_types"])),
            ("state", "=", "posted"),
            ("date", ">=", self.return_id.date_from),
            ("date", "<=", self.return_id.date_to),
            *spec["domain"],
        ])

    def _move_number(self, move):
        """The number KRA would know this document by.

        On sales that is our own invoice number. On purchases it is the
        supplier's, which Odoo keeps as the bill reference, not the entry name.
        """
        self.ensure_one()
        if SECTIONS[self.section]["side"] == "sale":
            return _normalise_number(move.name)
        return _normalise_number(move.ref or move.payment_reference or move.name)

    def _match_kra_rows(self):
        """Pair each KRA row with the document it describes.

        Sales rows are keyed on our own invoice number, which is unique.
        Purchase rows are keyed on the supplier's number *and* the supplier's
        PIN, because two suppliers can both have issued an "INV-001"; the PIN
        is on every KRA purchase row, and on our side it is the commercial
        partner's, not a contact person's. A purchase row whose PIN matches
        nobody falls back to the number alone, so a supplier whose PIN we have
        wrong is reported as a PIN variance rather than as missing.
        """
        self.ensure_one()
        is_sale = SECTIONS[self.section]["side"] == "sale"
        moves = self._candidate_moves()
        by_key = defaultdict(lambda: self.env["account.move"])
        by_number = defaultdict(lambda: self.env["account.move"])
        for move in moves:
            number = self._move_number(move)
            by_number[number] |= move
            by_key[(number, _normalise_pin(move.commercial_partner_id.vat))] |= move

        currency = self.company_id.currency_id
        for line in self.line_ids.filtered(lambda line: line.kra_invoice_no):
            candidates = self.env["account.move"]
            if not is_sale and line.kra_pin:
                candidates = by_key.get((line.kra_invoice_no, line.kra_pin), candidates)
            if not candidates:
                candidates = by_number.get(line.kra_invoice_no, candidates)
            if not candidates:
                line._apply_match(self.env["account.move"], "kra_only")
                continue

            move = candidates[0]
            partner_pin = _normalise_pin(move.commercial_partner_id.vat)
            if line.kra_pin and partner_pin and partner_pin != line.kra_pin:
                # KRA's FAQ calls this out explicitly: a supplier capturing the
                # wrong PIN is a named reason to leave input tax unclaimed.
                line._apply_match(move, "pin_variance")
                continue

            # KRA's figures are in shillings. amount_untaxed and amount_tax are
            # in the invoice currency, so a USD export or a foreign bill would
            # be compared as dollars against shillings; the *_signed fields
            # are the company-currency amounts, signed by document type.
            taxable_gap = currency.compare_amounts(
                abs(move.amount_untaxed_signed), line.kra_taxable_amount)
            tax_gap = currency.compare_amounts(
                abs(move.amount_tax_signed), line.kra_tax_amount)
            line._apply_match(
                move, "amount_variance" if (taxable_gap or tax_gap) else "matched")

    def _find_odoo_only(self):
        """Documents we have posted that no KRA file on this return mentions.

        Worked out for the whole return, then attributed to one batch each.
        Sections overlap in what they face -- both sales sections face
        customer invoices, three purchase sections face bills -- so a batch
        deciding on its own would report every document its sibling matched
        as missing, and the lumpsum figure would approach the company's whole
        turnover. Each unmatched document is listed once, under the first
        section in SECTIONS order whose domain it satisfies.
        """
        Line = self.env["ke.vat.itax.line"]
        for vat_return in self.return_id:
            batches = vat_return.batch_ids.sorted(
                key=lambda batch: SECTION_ORDER.index(batch.section))
            # Documents a KRA row accounts for, on any section of the return.
            known = set(batches.line_ids.filtered(
                lambda line: line.kra_invoice_no and line.move_id
            ).mapped("move_id").ids)
            # Last run's Odoo-only rows, kept where they still apply so that a
            # deferral or a note placed on one survives the next match.
            previous, duplicates = {}, Line
            for line in batches.line_ids.filtered(lambda line: not line.kra_invoice_no):
                if line.move_id.id in previous:
                    duplicates |= line   # cannot happen after this rewrite; tolerated
                else:
                    previous[line.move_id.id] = line

            claimed = set(known)
            for batch in batches:
                missing = batch._candidate_moves().filtered(
                    lambda move: move.id not in claimed)
                claimed.update(missing.ids)
                to_create = []
                for move in missing:
                    line = previous.pop(move.id, None)
                    if line is None:
                        to_create.append({
                            "batch_id": batch.id,
                            "move_id": move.id,
                            "match_state": "odoo_only",
                        })
                    elif line.batch_id != batch:
                        line.batch_id = batch
                if to_create:
                    Line.create(to_create)
            # Whatever is left was matched by a KRA row since, or is no longer
            # a candidate; either way it is not an exception any more. A
            # decision taken on it -- the claim was deferred, a note was
            # written -- is about the document, so it moves to the KRA row
            # that now describes the same document.
            leftovers = Line.concat(*previous.values()) | duplicates
            for line in leftovers:
                successor = batches.line_ids.filtered(
                    lambda l, move=line.move_id: l.kra_invoice_no and l.move_id == move)[:1]
                if not successor:
                    continue
                handover = {}
                if (line.claim_state in ("defer", "prohibited")
                        and successor.claim_state == "claim"):
                    handover["claim_state"] = line.claim_state
                if line.note and not successor.note:
                    handover["note"] = line.note
                if handover:
                    successor.write(handover)
            leftovers.unlink()


class KeVatItaxLine(models.Model):
    _name = "ke.vat.itax.line"
    _description = "KRA iTax Reconciliation Line"
    _order = "match_state, kra_invoice_date, id"
    _check_company_auto = True

    batch_id = fields.Many2one(
        "ke.vat.itax.batch", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(
        related="batch_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="batch_id.currency_id")
    section = fields.Selection(related="batch_id.section", store=True)

    # --- what KRA has
    kra_pin = fields.Char(string="KRA PIN", index=True)
    kra_partner_name = fields.Char(string="KRA Name")
    kra_invoice_no = fields.Char(string="KRA Invoice No", index=True)
    kra_invoice_date = fields.Date(string="KRA Invoice Date")
    kra_taxable_amount = fields.Monetary(string="KRA Taxable")
    kra_tax_amount = fields.Monetary(string="KRA VAT")
    kra_entry_no = fields.Char(string="Customs Entry No")

    # --- what we have
    move_id = fields.Many2one(
        "account.move", string="Document", index="btree_not_null",
        check_company=True)
    partner_id = fields.Many2one(related="move_id.partner_id", store=True)
    odoo_taxable_amount = fields.Monetary(
        string="Our Taxable", compute="_compute_odoo_amounts", store=True)
    odoo_tax_amount = fields.Monetary(
        string="Our VAT", compute="_compute_odoo_amounts", store=True)

    match_state = fields.Selection(
        [
            ("matched", "Matched"),
            ("kra_only", "In KRA, not in Odoo"),
            ("odoo_only", "In Odoo, not in KRA"),
            ("amount_variance", "Amount differs"),
            ("pin_variance", "PIN differs"),
        ],
        default="kra_only", required=True, index=True)
    claim_state = fields.Selection(
        [
            ("claim", "Claim this period"),
            ("defer", "Defer"),
            ("exclude_special_table", "Excluded: VAT special table"),
            ("prohibited", "Prohibited input tax"),
        ],
        compute="_compute_claim_state", store=True, readonly=False,
        help="Input tax only. Deferred claims stay claimable until their "
             "deadline; excluded ones must be kept out of the return or it is "
             "rejected at filing.")
    claim_deadline = fields.Date(
        compute="_compute_claim_deadline", store=True,
        help="Input tax is claimable for six months after the end of the "
             "period in which the supply occurred.")
    note = fields.Char()
    claim_expiring = fields.Boolean(
        compute="_compute_claim_expiring",
        help="A deferred claim with a month or less left on its six-month "
             "window. Decorated in the list so it cannot be scrolled past.")

    @api.depends("claim_deadline", "claim_state")
    def _compute_claim_expiring(self):
        horizon = fields.Date.context_today(self) + relativedelta(days=30)
        for line in self:
            line.claim_expiring = bool(
                line.claim_state == "defer"
                and line.claim_deadline
                and line.claim_deadline <= horizon)

    @api.depends("move_id.amount_untaxed_signed", "move_id.amount_tax_signed")
    def _compute_odoo_amounts(self):
        # Company currency, like KRA's figures and like the Monetary fields'
        # currency_id; amount_untaxed would show a USD invoice as shillings.
        for line in self:
            line.odoo_taxable_amount = abs(line.move_id.amount_untaxed_signed or 0.0)
            line.odoo_tax_amount = abs(line.move_id.amount_tax_signed or 0.0)

    @api.depends("kra_invoice_date", "move_id.invoice_date")
    def _compute_claim_deadline(self):
        for line in self:
            invoice_date = line.kra_invoice_date or line.move_id.invoice_date
            if not invoice_date or SECTIONS.get(line.section, {}).get("side") != "purchase":
                line.claim_deadline = False
                continue
            # End of the tax period the supply fell in, plus six months.
            period_end = invoice_date + relativedelta(day=31)
            line.claim_deadline = period_end + relativedelta(months=6)

    def _kra_key(self):
        """What identifies this KRA row across re-imports."""
        self.ensure_one()
        return (self.kra_invoice_no, self.kra_pin)

    def _supplier(self):
        """The supplier this row is about, as a commercial entity.

        Through the document where there is one -- a bill addressed to a
        contact person still belongs to the contact's company, and that is
        where the special-table flag lives. A row KRA has and we do not still
        carries KRA's PIN, so the supplier is looked up by it: a listed
        supplier whose bill never reached us must not slip through unflagged.
        """
        self.ensure_one()
        if self.move_id:
            return self.move_id.commercial_partner_id
        if self.kra_pin:
            return self.env["res.partner"].search(
                [("vat", "=ilike", self.kra_pin)], limit=1).commercial_partner_id
        return self.env["res.partner"]

    @api.depends("partner_id.commercial_partner_id.ke_vat_special_table",
                 "kra_pin", "section", "match_state")
    def _compute_claim_state(self):
        for line in self:
            spec = SECTIONS.get(line.section, {})
            if spec.get("side") != "purchase":
                line.claim_state = False
                continue
            # KRA relaxes the special-table check for credit notes, so a
            # flagged supplier's credit note stays claimable.
            is_credit_note = line.section == "purchases_credit_notes"
            if line._supplier().ke_vat_special_table and not is_credit_note:
                line.claim_state = "exclude_special_table"
            elif not line.claim_state or line.claim_state == "exclude_special_table":
                # Newly created, or the supplier has come off the table.
                line.claim_state = "claim"

    def _apply_match(self, move, match_state):
        self.ensure_one()
        self.write({"move_id": move.id if move else False,
                    "match_state": match_state})

    def action_open_document(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(_(
                "KRA has this invoice and we do not, so there is nothing to "
                "open. Either it was never posted here, or the supplier "
                "captured a different invoice number."))
        return self.move_id._get_records_action()


class KeVatReturn(models.Model):
    _inherit = "ke.vat.return"

    def _ke_refresh_odoo_only(self):
        """Recompute "in Odoo, not in KRA" across every import on these returns.

        Lives here rather than in ke_vat_return.py because it is
        reconciliation logic: the return only lends it the set of batches.
        """
        for vat_return in self:
            vat_return.batch_ids._find_odoo_only()
        return True
