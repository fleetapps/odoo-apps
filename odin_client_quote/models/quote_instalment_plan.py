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

    def schedule(self, total):
        """``total`` paid like a loan: ``months`` equal payments, the first
        one now (it is the deposit), the financing -- interest on the whole
        premium for every month, plus any monthly fee -- spread over all of
        them. ``deposit`` is kept as the first payment for the reports that
        print one."""
        self.ensure_one()
        currency = self.currency_id or self.env.company.currency_id
        months = self.months
        financing = (total * (self.monthly_rate or 0.0) / 100.0 * months
                     + (self.monthly_fee or 0.0) * months)
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
