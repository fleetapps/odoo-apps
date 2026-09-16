1. **Set the cost control analytic plan** in Accounting → Configuration →
   Settings → Cost Control. This matters more than it looks: an analytic
   distribution key can name several accounts at once, like `"5,12"`, meaning
   100% on account 5 in one plan *and* 100% on account 12 in another. They are
   two dimensions of the same cost, not a split of it — so exactly one plan
   drives cost control. Without this setting the first account of each key is
   used, which is arbitrary but at least never doubles a figure.
2. Give the team *Cost Control / User*, and whoever confirms and revises
   budgets *Cost Control / Manager*.
3. The daily *Cost Control: close settled commitments* scheduled action keeps
   the open list readable. It does not change any amount — those follow the
   purchase order line.
