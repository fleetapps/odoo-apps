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
        help="Number of monthly instalments after the deposit.",
    )
    deposit_percent = fields.Float(
        string="Deposit (%)",
        help="Share of the premium payable before cover starts. Leave at zero "
        "where the whole premium is spread over the instalments.",
    )
    monthly_rate = fields.Float(
        string="Interest (% per month)",
        help="Charged on the balance left after the deposit, for each month of "
        "the plan.",
    )
    monthly_fee = fields.Monetary(
        string="Fee per month",
        currency_field="currency_id",
        help="Flat financing charge added for each month of the plan, whatever "
        "the premium.",
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True
    )

    _months_positive = models.Constraint(
        "CHECK (months > 0)",
        "An instalment plan needs at least one instalment.",
    )

    def schedule(self, total):
        """Break ``total`` into a deposit and ``months`` equal instalments.

        The last instalment absorbs the rounding so the parts always add back
        up to what is quoted.
        """
        self.ensure_one()
        currency = self.currency_id or self.env.company.currency_id
        deposit = currency.round(total * (self.deposit_percent or 0.0) / 100.0)
        financed = total - deposit
        interest = financed * (self.monthly_rate or 0.0) / 100.0 * self.months
        payable = currency.round(
            total + interest + (self.monthly_fee or 0.0) * self.months
        )
        instalment = currency.round((payable - deposit) / self.months)
        last = currency.round(payable - deposit - instalment * (self.months - 1))
        return {
            "plan": self,
            "months": self.months,
            "deposit": deposit,
            "instalment": instalment,
            "last_instalment": last,
            "payable": payable,
            "financing": payable - total,
        }
