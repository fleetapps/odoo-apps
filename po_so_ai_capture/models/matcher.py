# -*- coding: utf-8 -*-
"""Matching ladder - most reliable first, each rung sets method + confidence:
  partner:  email domain of a known contact > exact name > fuzzy name
  product:  learned alias (per customer!) > internal ref > barcode > fuzzy name
stdlib difflib keeps the module dependency-free (census lesson: server-side
installs killed competitor adoption)."""
import difflib
from odoo import api, models


class PoCaptureMatcher(models.AbstractModel):
    _name = "po.capture.matcher"
    _description = "PO Capture Matcher"

    # ---------------------------------------------------------- partner
    @api.model
    def match_partner(self, name, email_from):
        Partner = self.env["res.partner"]
        # 1) sender email -> contact -> commercial partner
        if email_from:
            email = email_from.split("<")[-1].rstrip(">").strip()
            p = Partner.search([("email", "=ilike", email)], limit=1)
            if p:
                return p.commercial_partner_id, 0.97
            domain = email.split("@")[-1]
            if domain and "." in domain:
                p = Partner.search([("email", "=ilike", f"%@{domain}"),
                                    ("is_company", "=", False)], limit=1)
                if p:
                    return p.commercial_partner_id, 0.90
        if not name:
            return Partner, 0.0
        # 2) exact then fuzzy company name
        p = Partner.search([("name", "=ilike", name.strip()),
                            ("is_company", "=", True)], limit=1)
        if p:
            return p, 0.95
        best, score = Partner, 0.0
        for c in Partner.search([("is_company", "=", True),
                                 ("customer_rank", ">", 0)], limit=2000):
            s = difflib.SequenceMatcher(
                None, name.lower(), (c.name or "").lower()).ratio()
            if s > score:
                best, score = c, s
        return (best, score) if score >= 0.80 else (Partner, score)

    # ---------------------------------------------------------- product
    @api.model
    def match_product(self, partner, customer_code, description):
        Product = self.env["product.product"]
        code = (customer_code or "").strip()
        # 1) learned per-customer alias - the compounding rung
        if partner and code:
            alias = self.env["po.capture.sku.alias"].search(
                [("partner_id", "=", partner.id),
                 ("customer_code", "=ilike", code)], limit=1)
            if alias:
                conf = min(0.99, 0.90 + 0.02 * alias.hit_count)
                return alias.product_id, conf, "alias"
        # 2) our internal reference / 3) barcode
        if code:
            p = Product.search([("default_code", "=ilike", code)], limit=1)
            if p:
                return p, 0.95, "sku"
            p = Product.search([("barcode", "=", code)], limit=1)
            if p:
                return p, 0.95, "barcode"
        # 4) fuzzy description
        if description:
            best, score = Product, 0.0
            for c in Product.search([("sale_ok", "=", True)], limit=3000):
                s = difflib.SequenceMatcher(
                    None, description.lower(), (c.name or "").lower()).ratio()
                if s > score:
                    best, score = c, s
            if score >= 0.70:
                return best, score * 0.9, "fuzzy"  # cap fuzzy below exact rungs
        return Product, 0.0, False
