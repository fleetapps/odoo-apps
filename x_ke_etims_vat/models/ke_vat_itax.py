# -*- coding: utf-8 -*-
"""iTax rows placed by the numbers eTIMS gave the documents.

KRA pre-fills the return from eTIMS, so a row's invoice number may be the CU
invoice number (SCU ID / receipt number), or the trader invoice number we sent
with the sale, rather than the Odoo invoice number the VAT engine keys on. On
purchases it may be the supplier's CU invoice number rather than the bill
reference. The VAT engine matches first; what it leaves as "in KRA, not in
Odoo" is tried against those numbers before it is reported missing.
"""

from collections import defaultdict

from odoo import api, fields, models

from odoo.addons.x_ke_vat.models.ke_vat_itax import SECTIONS, _normalise_pin


class KeVatItaxBatch(models.Model):
    _inherit = "ke.vat.itax.batch"

    def _match_kra_rows(self):
        super()._match_kra_rows()
        self._ke_etims_match_kra_rows()

    def _ke_etims_match_kra_rows(self):
        self.ensure_one()
        unplaced = self.line_ids.filtered(
            lambda line: line.kra_invoice_no and line.match_state == "kra_only")
        if not unplaced:
            return
        # A document another row of this file already accounts for is not a
        # candidate: one document, one row.
        claimed = self.line_ids.filtered(lambda line: line.kra_invoice_no).move_id
        moves = self._candidate_moves() - claimed
        is_sale = SECTIONS[self.section]["side"] == "sale"
        by_key = defaultdict(lambda: self.env["account.move"])
        by_number = defaultdict(lambda: self.env["account.move"])
        for move in moves:
            pin = _normalise_pin(move.commercial_partner_id.vat)
            for number in move._ke_etims_numbers():
                by_number[number] |= move
                by_key[(number, pin)] |= move

        for line in unplaced:
            candidates = self.env["account.move"]
            if not is_sale and line.kra_pin:
                candidates = by_key.get((line.kra_invoice_no, line.kra_pin), candidates)
            if not candidates:
                candidates = by_number.get(line.kra_invoice_no, candidates)
            # A bare trader invoice number can be two branches' numbers; only
            # a number that names one document places the row.
            candidates -= claimed
            if len(candidates) != 1:
                continue
            claimed |= candidates
            self._classify_match(line, candidates)


class KeVatItaxLine(models.Model):
    _inherit = "ke.vat.itax.line"

    ke_etims_number = fields.Char(compute="_compute_ke_etims_number", string="eTIMS No.")
    ke_etims_state = fields.Selection(related="move_id.ke_etims_state")

    @api.depends("move_id.ke_etims_cu_invoice", "move_id.ke_etims_supplier_invoice_ref")
    def _compute_ke_etims_number(self):
        for line in self:
            line.ke_etims_number = line.move_id and line.move_id._ke_etims_number()
