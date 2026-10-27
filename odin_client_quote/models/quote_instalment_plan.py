from odoo import fields, models


class QuoteInstalmentPlan(models.Model):
    _name = "odin.quote.instalment.plan"
    _description = "Instalment plan offered on a quotation"
    _order = "sequence, months, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    months = fields.Integer(
        string="Instalments", required=True, default=4,
        help="Number of equal monthly payments. The first is paid when cover "
        "starts: it is the deposit.",
    )
    deposit_percent = fields.Float(
        string="Deposit (%)",
        help="Not used: the first instalment is the deposit.",
    )
    monthly_rate = fields.Float(
        string="Interest (% per month)",
        help="Charged on the whole premium for each month of the plan, as on a "
        "loan, and spread equally over all the instalments.",
    )
    monthly_fee = fields.Monetary(
        string="Fee per month",
        currency_field="currency_id",
        help="Flat financing charge for each month of the plan, whatever the "
        "premium, spread equally over all the instalments.",
    )
    term_rate = fields.Float(
        string="Interest for the whole plan (%)",
        digits=(16, 4),
        help="One flat rate on the premium for the whole plan, the way premium "
        "financiers quote it -- NCBA charges 3.08 for 6 instalments, not 3.08 "
        "a month. Used for premiums no fixed fee below covers.",
    )
    min_premium = fields.Monetary(
        string="Smallest premium financed",
        currency_field="currency_id",
        help="Below this the plan is not offered: the financier will not lend "
        "less (NCBA: KES 25,000).",
    )
    fee_band_ids = fields.One2many(
        "odin.quote.instalment.band", "plan_id", string="Fixed fees by premium",
        help="A fixed facility fee for premiums in a range, charged in place "
        "of any interest or monthly fee.",
    )
    payment_term_id = fields.Many2one(
        "account.payment.term", string="Payment terms", readonly=True, copy=False,
        help="The invoice's due dates when a client pays this way: one equal "
        "instalment now and one a month after. Made when first used.",
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True
    )

    _months_positive = models.Constraint(
        "CHECK (months > 0)",
        "An instalment plan needs at least one instalment.",
    )

    def _odin_available(self, total):
        """Whether this plan can finance a premium of ``total`` at all."""
        self.ensure_one()
        currency = self.currency_id or self.env.company.currency_id
        return not self.min_premium or currency.compare_amounts(total, self.min_premium) >= 0

    def _odin_financing(self, total):
        """What paying ``total`` this way costs: the fixed fee of the band the
        premium falls in, otherwise interest for the whole plan, interest per
        month and any monthly fee."""
        self.ensure_one()
        band = self.fee_band_ids.filtered(
            lambda b: b.amount_from <= total and (not b.amount_to or total < b.amount_to))[:1]
        if band:
            return band.fee
        months = self.months
        return (total * (self.term_rate or 0.0) / 100.0
                + total * (self.monthly_rate or 0.0) / 100.0 * months
                + (self.monthly_fee or 0.0) * months)

    def schedule(self, total):
        """``total`` paid like a loan: ``months`` equal payments, the first
        one now (it is the deposit), the financing (see ``_odin_financing``)
        spread over all of them. ``deposit`` is kept as the first payment for
        the reports that print one. Check ``_odin_available`` first: a plan
        is not offered below its smallest premium."""
        self.ensure_one()
        currency = self.currency_id or self.env.company.currency_id
        months = self.months
        financing = self._odin_financing(total)
        instalment = currency.round((total + financing) / months)
        payable = currency.round(instalment * months)
        return {
            "plan": self,
            "months": months,
            "deposit": instalment,
            "instalment": instalment,
            "last_instalment": instalment,
            "payable": payable,
            "financing": payable - total,
        }

    def _odin_payment_term(self):
        """Payment terms of ``months`` equal instalments, a month apart, the
        first due on the invoice date."""
        self.ensure_one()
        if self.payment_term_id:
            return self.payment_term_id
        share = round(100.0 / self.months, 6)
        lines = []
        for index in range(self.months):
            value = share if index < self.months - 1 else round(100.0 - share * (self.months - 1), 6)
            lines.append(fields.Command.create({
                "value": "percent", "value_amount": value,
                "delay_type": "days_after", "nb_days": 30 * index,
            }))
        term = self.env["account.payment.term"].sudo().create({
            "name": self.env._("%s equal monthly instalments", self.months),
            "company_id": self.company_id.id,
            "line_ids": lines,
        })
        self.sudo().payment_term_id = term
        return term


class QuoteInstalmentBand(models.Model):
    _name = "odin.quote.instalment.band"
    _description = "Fixed instalment fee for a range of premiums"
    _order = "plan_id, amount_from, id"

    plan_id = fields.Many2one(
        "odin.quote.instalment.plan", required=True, ondelete="cascade", index=True
    )
    amount_from = fields.Monetary(
        string="Premium from", required=True, currency_field="currency_id"
    )
    amount_to = fields.Monetary(
        string="Premium below", currency_field="currency_id",
        help="Up to, but not including, this premium. Empty: no upper limit.",
    )
    fee = fields.Monetary(
        string="Fee", required=True, currency_field="currency_id",
        help="Charged once for the whole plan and spread over its instalments.",
    )
    currency_id = fields.Many2one(related="plan_id.currency_id")
