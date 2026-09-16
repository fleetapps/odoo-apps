# -*- coding: utf-8 -*-
"""One table that decides what a Kenyan supply is, for every consumer.

The VAT3 return and the eTIMS wire ask the same question about every line of
every invoice -- what kind of supply is this -- and answer it in different
vocabularies. The return wants a box number. KRA's OSCU API wants a tax type
code, A through E. Derive those separately and they will drift, and the drift
surfaces as a variance on a return whose figures KRA will not let you edit.

So they are derived from one place, and this is it.

Why the rows carry dates
------------------------
Kenya cut VAT on petrol, diesel and kerosene from 16% to 8% on 16 April 2026
and has extended the reduction twice, currently to 14 October 2026. On the
fifteenth those products go back to 16%, which changes the KRA band they
transmit under and the VAT3 box they land in.

Instances are cut from a baked template with ``createdb --template`` and are
never upgraded -- there is no ``-u`` anywhere in the deployment. A mapping
resolved once at install time would therefore be wrong forever on every
instance deployed before that date. The rows ship with both windows already in
them, and the resolver picks by date at the moment it is asked.

Two bases, because the flip is not about tags
---------------------------------------------
A tag named "8% Sales Base" means the 8% band whenever it is used; its meaning
does not change on 15 October. What changes is *which supplies belong to it* --
petroleum moves from the 8% tax to the 16% one. So the table is keyed two ways:

``tag``        an l10n_ke tax tag, as stamped on a posted journal item. Stable.
               This is what makes an already-posted line resolve to the same
               band on the wire and the same box on the return.
``item_code``  the rate letter on ``l10n_ke.item.code``, which is what decides a
               product's tax in the first place. These rows are date-ranged and
               carry the fuel flip.

The item_code rows also carry a correction. l10n_ke's letters and KRA's letters
collide and disagree: Odoo's selection is C=Zero Rated, E=Exempted, B=Taxable
at 8%, while the OSCU specification defines A=Exempt, B=16%, C=0%, D=Non-VAT,
E=8%. Only C agrees. Map Odoo's field straight onto ``taxTyCd`` and exempt
supplies transmit as 8% and 8% petroleum transmits as 16% -- an over-declaration
on a return that cannot be corrected afterwards. The rows below translate.

Tag names come from l10n_ke's own tax template, and are data rather than
constants precisely so that a rename upstream is a row edit and not a release.
Verify them against the ``PROBE <tax>: base tags = [...]`` lines in a real bake
log before trusting any of it.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# KRA OSCU Specification v2.0, section 4.1 "Tax Type".
KRA_TAX_TYPE_CODES = [
    ("A", "A - Exempt"),
    ("B", "B - 16%"),
    ("C", "C - Zero rated (0%)"),
    ("D", "D - Non-VAT"),
    ("E", "E - 8%"),
]


class KeTaxClassification(models.Model):
    _name = "ke.tax.classification"
    _description = "Kenyan Supply Classification"
    _order = "basis, sequence, date_from desc, id"

    name = fields.Char(compute="_compute_name", store=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    basis = fields.Selection(
        [("tag", "Tax Tag"), ("item_code", "KRA Item Code Rate")],
        required=True, index=True,
        help="What this row is keyed on. Tag rows classify a posted journal "
             "item; item-code rows classify a product and are where a rate "
             "change over time is recorded.")

    tag_name = fields.Char(
        string="Tax Tag", index=True,
        help="The l10n_ke tax tag exactly as it appears on a journal item. "
             "Held as text rather than a link because the tag records are "
             "created per company when the chart of accounts loads.")
    item_tax_rate = fields.Selection(
        [("C", "C - Zero Rated (Odoo)"),
         ("E", "E - Exempted (Odoo)"),
         ("B", "B - Taxable at 8% (Odoo)")],
        string="Odoo Item Code Rate",
        help="The letter l10n_ke puts on l10n_ke.item.code. These letters do "
             "not mean what KRA's letters mean; that is what this table "
             "translates.")

    date_from = fields.Date(help="Empty means from the beginning.")
    date_to = fields.Date(help="Empty means until further notice.")

    kra_band = fields.Selection(
        KRA_TAX_TYPE_CODES, string="KRA Tax Type",
        help="The code transmitted to eTIMS as taxTyCd. Empty where the tag "
             "is not a supply band at all, as with withholding VAT.")
    vat3_box = fields.Char(
        string="VAT3 Box",
        help="The report line code on the Kenyan VAT3 definition, e.g. box_1.")
    tax_template_id = fields.Char(
        string="Tax Template",
        help="The l10n_ke tax a supply of this class should carry, by template "
             "id, e.g. ST16. Resolved per company as account.{id}_{template}.")
    side = fields.Selection([("sale", "Sale"), ("purchase", "Purchase")])
    label = fields.Selection(
        [("base", "Base"), ("tax", "Tax")], string="Column",
        help="Which column of the VAT3 box this tag feeds. A box carries a "
             "turnover figure and a VAT figure, and they are different tags.")
    rate = fields.Float(digits=(5, 2), help="Statutory rate in force, percent.")
    note = fields.Char(help="Why this row says what it says.")

    _tag_or_item = models.Constraint(
        "CHECK((basis = 'tag' AND tag_name IS NOT NULL) OR "
        "(basis = 'item_code' AND item_tax_rate IS NOT NULL))",
        "A tag row needs a tag name and an item-code row needs a rate letter.")

    @api.depends("basis", "tag_name", "item_tax_rate", "date_from", "date_to")
    def _compute_name(self):
        for record in self:
            key = record.tag_name or record.item_tax_rate or "?"
            window = ""
            if record.date_from or record.date_to:
                window = " [%s to %s]" % (
                    record.date_from or "...", record.date_to or "...")
            record.name = "%s%s" % (key, window)

    @api.constrains("basis", "tag_name", "item_tax_rate", "date_from", "date_to")
    def _check_no_overlap(self):
        """Two rows answering the same question for the same day is a silent
        coin toss, so it is refused at write time instead."""
        for record in self:
            key_field = "tag_name" if record.basis == "tag" else "item_tax_rate"
            others = self.search([
                ("id", "!=", record.id),
                ("basis", "=", record.basis),
                (key_field, "=", record[key_field]),
            ])
            for other in others:
                starts_after = (record.date_from and other.date_to
                                and record.date_from > other.date_to)
                ends_before = (record.date_to and other.date_from
                               and record.date_to < other.date_from)
                if not starts_after and not ends_before:
                    raise ValidationError(_(
                        "%(a)s and %(b)s both classify %(key)s over the same "
                        "dates. One of them has to end before the other "
                        "starts.",
                        a=record.display_name, b=other.display_name,
                        key=record[key_field]))

    # --------------------------------------------------------------- lookup

    @api.model
    def _resolve(self, basis, key, date=None):
        """The row in force for that key on that date, or an empty recordset."""
        date = date or fields.Date.context_today(self)
        key_field = "tag_name" if basis == "tag" else "item_tax_rate"
        return self.search([
            ("basis", "=", basis),
            (key_field, "=", key),
            "|", ("date_from", "=", False), ("date_from", "<=", date),
            "|", ("date_to", "=", False), ("date_to", ">=", date),
        ], limit=1)

    @api.model
    def band_for_tag(self, tag_name, date=None):
        """KRA taxTyCd for a tag, or False where the tag is not a supply band."""
        return self._resolve("tag", tag_name, date).kra_band or False

    @api.model
    def box_for_tag(self, tag_name, date=None):
        """VAT3 report line code for a tag, e.g. 'box_1'."""
        return self._resolve("tag", tag_name, date).vat3_box or False

    @api.model
    def band_for_item_rate(self, item_tax_rate, date=None):
        """KRA taxTyCd for a product, translated out of l10n_ke's letters.

        This is the one that changes on 15 October 2026, when the reduced rate
        on petroleum lapses.
        """
        row = self._resolve("item_code", item_tax_rate, date)
        if not row:
            return False
        _logger.debug(
            "ke_tax_classification: item rate %s on %s resolves to KRA band %s "
            "via %s", item_tax_rate, date or "today", row.kra_band, row.name)
        return row.kra_band

    @api.model
    def tag_name_for(self, vat3_box, side, label="base", date=None):
        """The tag a given box reads, on a given side, in a given column.

        Used where a correction has to name a tag it must not hardcode -- see
        res_company.ke_apply_tax_corrections. A box carries both a turnover and
        a VAT figure, so the column is part of the question.
        """
        date = date or fields.Date.context_today(self)
        row = self.search([
            ("basis", "=", "tag"),
            ("vat3_box", "=", vat3_box),
            ("side", "=", side),
            ("label", "=", label),
            "|", ("date_from", "=", False), ("date_from", "<=", date),
            "|", ("date_to", "=", False), ("date_to", ">=", date),
        ], limit=1)
        return row.tag_name or False
