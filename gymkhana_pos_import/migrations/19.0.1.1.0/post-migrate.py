"""After the bars moved to Bar Control:

- the Dhostana bars get any POS setup still missing, as a fresh install
  would (only empty values are filled, nothing is guessed);
- days imported before Bar Control knew about them: their deliveries (and the
  returns of undone days) get their trading day, and every posted day is
  recorded per bar, "no sales" for a bar the report had nothing for.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.gymkhana_pos_import.models.res_company import SEED_COMPANY


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env["res.company"].search([("name", "=", SEED_COMPANY)]):
        company._pos_import_seed_bars()
    imports = env["pos.import"].search([("state", "in", ("posted", "cancelled"))], order="business_date, id")
    for imp in imports:
        day = imp.business_date
        (imp.order_ids.picking_ids | imp.return_picking_ids).filtered(
            lambda picking: not picking.bar_business_date
        ).write({"bar_business_date": day})
        if imp.state != "posted":
            continue
        sold_at = imp.line_ids.bar_id
        for bar in imp._pos_bar_records():
            bar._mark_pos_posted(day, source=imp.name, no_sales=bar not in sold_at).pos_import_id = imp
