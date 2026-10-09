Liquidated damages on EPC contracts: recorded once as a clause, accrued
automatically, capped where the contract caps them, and deducted on the
document the counterparty actually pays against.

Standard Odoo has nothing for this in either edition. The arithmetic is not
hard — it is the *bookkeeping around* it that goes wrong on real projects:
extensions of time that nobody folds back into the completion date, a cap
discovered after it was breached, and concessions granted verbally that leave
no trace at audit.

This module handles both directions. Damages the employer levies on us are
deducted from a customer invoice; damages we recover from a subcontractor are
deducted from their bill. The two are kept apart, because netting somebody
else's liability against ours flatters the project.
