# -*- coding: utf-8 -*-
"""Fuzzy vendor matching: exact VAT beats everything, then normalized-name
similarity via difflib (stdlib - no external dependency)."""
import difflib
from odoo import api, models


class Matcher(models.AbstractModel):
    _name = "ai.bill.matcher"
    _description = "Vendor Matcher"

    @api.model
    def match_partner(self, name, vat):
        Partner = self.env["res.partner"]
        if vat:
            p = Partner.search([("vat", "=ilike", vat.replace(" ", ""))], limit=1)
            if p:
                return p, 0.99
        if not name:
            return Partner, 0.0
        candidates = Partner.search([("supplier_rank", ">", 0)], limit=2000)
        norm = name.strip().lower()
        best, score = Partner, 0.0
        for c in candidates:
            s = difflib.SequenceMatcher(None, norm, (c.name or "").lower()).ratio()
            if s > score:
                best, score = c, s
        return (best, score) if score >= 0.75 else (Partner, score)
