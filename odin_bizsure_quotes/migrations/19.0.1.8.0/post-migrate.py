"""APA's benefits by limit, and the shortened key terms, on plans installed
before them.

APA's outpatient benefits differ by outpatient limit, and its child-only
plan's inpatient benefits by inpatient limit. The long terms printed them in a
table; the key terms do not, and the plans' notes pointed to "the terms
below". Each limit now has its own note -- "Outpatient benefits on the KES
50,000 limit" -- and a quotation keeps the note of the limit quoted, so its
What's covered list shows the client's own sub-limits.

Loading the data adds the new notes (a noupdate record that is missing is
created). This writes the first limit's text over the old note, which the
templates already have, and puts the new notes where the old one stands. An
old note the office has reworded is left as it is.

The key terms are written again, shortened, with the 19.0.1.7.0 step, which
leaves what is already right as it is.
"""

import importlib.util
import logging
import os
import re

from lxml import etree

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

MODULE = "odin_bizsure_quotes"
HERE = os.path.dirname(__file__)
#: what the old notes start with
POINTERS = ("Outpatient cover is priced per person", "Benefits for each outpatient limit",
            "Child-only cover is available for inpatient limits")
#: a note added for one limit: ``<old note's id>_op75k``
BY_LIMIT = re.compile(r"^(?P<base>.+_line_\d+)_(?:op|ip)\d+k$")


def _notes():
    """{old note's external id: (its new text, [the added notes' ids])}."""
    tree = etree.parse(os.path.join(HERE, os.pardir, os.pardir, "data", "sale_order_template.xml"))
    texts, added = {}, {}
    for record in tree.iter("record"):
        if record.get("model") != "sale.order.template.line":
            continue
        name = record.find("field[@name='name']")
        texts[record.get("id")] = name.text if name is not None else ""
        found = BY_LIMIT.match(record.get("id"))
        if found:
            added.setdefault(found.group("base"), []).append(record.get("id"))
    return {base: (texts[base], ids) for base, ids in added.items()}


def _run_names_and_terms(cr, version):
    path = os.path.join(HERE, os.pardir, "19.0.1.7.0", "post-migrate.py")
    spec = importlib.util.spec_from_file_location("odin_bizsure_quotes_migrate_1_7", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.migrate(cr, version)


def migrate(cr, version):
    _run_names_and_terms(cr, version)
    env = api.Environment(cr, SUPERUSER_ID, {})
    rewritten = kept = 0
    for base_id, (text, added_ids) in _notes().items():
        base = env.ref("%s.%s" % (MODULE, base_id), raise_if_not_found=False)
        if not base:
            continue
        if (base.name or "").startswith(POINTERS):
            base.name = text
            rewritten += 1
        elif base.name != text:
            kept += 1
            _logger.warning("%s: %s was reworded by hand and is kept as it is",
                            MODULE, base_id)
        for added_id in added_ids:
            line = env.ref("%s.%s" % (MODULE, added_id), raise_if_not_found=False)
            if line and line.sale_order_template_id == base.sale_order_template_id:
                # Beside the old note, wherever the template has it now.
                line.sequence = base.sequence
    _logger.info("%s: APA benefits by limit on %s notes (%s kept as reworded)",
                 MODULE, rewritten, kept)
