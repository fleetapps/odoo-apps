"""The import's own bars (pos.import.bar) become the bars of Bar Control
(odin.bar, from odin_bar_base, which Odoo installs just before this module).

Each old bar is matched to an existing Bar Control bar by POS group or
location, or created from it, keeping every setting. Import lines and sale
orders are then pointed at the new bars, before this module's models are
loaded with their foreign keys to odin_bar.
"""
import logging
import re

from odoo import SUPERUSER_ID, api
from odoo.tools import sql

_logger = logging.getLogger(__name__)

# (table, column) that pointed at pos_import_bar
REFERENCES = [("pos_import_line", "bar_id"), ("sale_order", "pos_import_bar_id")]


def _bar_code(env, old):
    """ISS/RET/SAL naming code of the bar: 'BB' from SAL-BB or '(BB)'."""
    candidates = []
    picking_type = env["stock.picking.type"].browse(old["picking_type_id"]).exists()
    if picking_type.sequence_code and picking_type.sequence_code.upper().startswith("SAL-"):
        candidates.append(picking_type.sequence_code[4:])
    location = env["stock.location"].browse(old["location_id"]).exists()
    found = re.search(r"\(([A-Za-z0-9]+)\)\s*$", location.name or "")
    if found:
        candidates.append(found.group(1))
    candidates.append(old["pos_suffix"])
    Bar = env["odin.bar"].with_context(active_test=False)
    for code in candidates:
        code = (code or "").strip().upper()
        if code and not Bar.search_count([("company_id", "=", old["company_id"]), ("code", "=", code)]):
            return code
    base = (candidates[-1] or "BAR").strip().upper()
    index = 2
    while Bar.search_count([("company_id", "=", old["company_id"]), ("code", "=", f"{base}{index}")]):
        index += 1
    return f"{base}{index}"


def _new_bar(env, old):
    Bar = env["odin.bar"].with_context(active_test=False)
    pos_vals = {
        "sale_type_id": old["picking_type_id"],
        "partner_id": old["delivery_partner_id"],
        "analytic_account_id": old["analytic_account_id"],
    }
    bar = Bar.search(
        [("company_id", "=", old["company_id"]), ("pos_group_name", "=", old["pos_group_name"])], limit=1
    ) or Bar.search([("location_id", "=", old["location_id"])], limit=1)
    if bar:
        # Already set up in Bar Control: fill what is empty, and the POS mapping
        # the import has been reading the report with.
        vals = {field: value for field, value in pos_vals.items() if value and not bar[field]}
        vals.update(pos_group_name=old["pos_group_name"], pos_suffix=old["pos_suffix"])
        bar.write(vals)
        _logger.info("POS import: bar %s now uses Bar Control bar %s", old["name"], bar.display_name)
        return bar
    bar = Bar.create(
        {
            **pos_vals,
            "name": old["name"],
            "code": _bar_code(env, old),
            "kind": "bar",
            "sequence": old["sequence"],
            "active": old["active"],
            "company_id": old["company_id"],
            "location_id": old["location_id"],
            "pos_group_name": old["pos_group_name"],
            "pos_suffix": old["pos_suffix"],
            "tz": "Africa/Nairobi",
        }
    )
    bar.action_autofill_setup()
    _logger.info("POS import: bar %s moved to Bar Control", old["name"])
    return bar


def _drop_foreign_keys(cr, table, column):
    cr.execute(
        """
        SELECT fk.conname
          FROM pg_constraint fk
          JOIN pg_class c1 ON fk.conrelid = c1.oid
          JOIN pg_class c2 ON fk.confrelid = c2.oid
          JOIN pg_attribute a1 ON a1.attrelid = c1.oid AND a1.attnum = fk.conkey[1]
         WHERE fk.contype = 'f' AND c1.relname = %s AND a1.attname = %s AND c2.relname = 'pos_import_bar'
        """,
        [table, column],
    )
    for (name,) in cr.fetchall():
        sql.drop_constraint(cr, table, name)


def migrate(cr, version):
    if not sql.table_exists(cr, "pos_import_bar"):
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute(
        """
        SELECT id, name, sequence, active, company_id, pos_group_name, pos_suffix,
               location_id, picking_type_id, delivery_partner_id, analytic_account_id
          FROM pos_import_bar
      ORDER BY sequence, id
        """
    )
    mapping = {old["id"]: _new_bar(env, old).id for old in cr.dictfetchall()}
    env.flush_all()
    for table, column in REFERENCES:
        if not sql.column_exists(cr, table, column):
            continue
        _drop_foreign_keys(cr, table, column)
        if mapping:
            cr.execute(
                f"""
                UPDATE {table} AS t SET {column} = m.new_id
                  FROM (SELECT unnest(%s) AS old_id, unnest(%s) AS new_id) AS m
                 WHERE t.{column} = m.old_id
                """,
                [list(mapping), list(mapping.values())],
            )
    _logger.info("POS import: %s bar(s) moved to Bar Control", len(mapping))
