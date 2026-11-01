"""Plan names and key terms as this version words them, on plans installed
before it.

The plans are noupdate, so an upgrade does not reach what earlier versions
installed:

* GA Community Health is named by its cover alone, ``GA Community Health –
  KES 1,000,000``, the other version ``..., with pandemic cover``; the online
  page offers the second as a switch on the first, telling them apart by
  name. The rate tables, their rate lines and the plan's inpatient heading
  are renamed with it.
* Every plan's key terms, reworded.

Written on the Cover Quotes products and rate tables, which put them on the
quotation templates and rate lines: written on the templates alone, they would
be put back the next time someone saved the product. Quotations already made
keep what they were sent with.
"""

import importlib.util
import logging
import os

from lxml import etree

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

MODULE = "odin_bizsure_quotes"
ROOT = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
DATA = ("cover_product_ga.xml", "cover_product_jubilee.xml", "cover_product_heritage.xml")
#: the fields taken from the data files, by model
FIELDS = {
    "odin.cover.table": ("name", "section_title"),
    "odin.cover.product": ("name",),
}
#: cover product external id prefix -> key terms (tools/key_terms.py)
GENERATED = (
    ("cover_hadhi_", "ga_hadhi"),
    ("cover_ga_", "ga_community"),
    ("cover_jcj_", "jubilee_j_care_junior"),
    ("cover_jc_", "jubilee_j_care"),
    ("cover_js_", "jubilee_j_senior"),
    ("cover_ha_", "heritage_heriafya"),
)
#: template external id prefix -> key terms, for the plans installed first
TEMPLATES = (
    ("sot_apa_jamii_plus_child_only_cover", "apa_child_only"),
    ("sot_apa_jamii_plus_family_cover_", "apa_family"),
    ("sot_birdview_bizsure_afya_", "birdview"),
)


def _key_terms():
    path = os.path.join(ROOT, "tools", "key_terms.py")
    spec = importlib.util.spec_from_file_location("odin_bizsure_quotes_key_terms", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data_values():
    """(model, external id, values) of the names and headings in the data."""
    for filename in DATA:
        for record in etree.parse(os.path.join(ROOT, "data", filename)).iter("record"):
            names = FIELDS.get(record.get("model"))
            if not names:
                continue
            values = {field.get("name"): field.text or False
                      for field in record.findall("field") if field.get("name") in names}
            yield record.get("model"), record.get("id"), values


def _rename_heading(table, old, new):
    """The plan's heading for the table, made from its section title."""
    lines = table.product_ids.template_id.sale_order_template_line_ids.filtered(
        lambda line: line.display_type == "line_section" and line.name == old)
    lines.write({"name": new})


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    key_terms = _key_terms()
    renamed = worded = 0
    for model, xml_id, values in _data_values():
        record = env.ref("%s.%s" % (MODULE, xml_id), raise_if_not_found=False)
        if not record or record._name != model:
            continue
        changed = {key: value for key, value in values.items() if record[key] != value}
        if model == "odin.cover.product":
            key = next((key for prefix, key in GENERATED if xml_id.startswith(prefix)), None)
            if key:
                changed["terms"] = key_terms.html(key)
                worded += 1
        if not changed:
            continue
        old_title = record.section_title if model == "odin.cover.table" else None
        record.write(changed)
        if old_title and "section_title" in changed:
            _rename_heading(record, old_title, changed["section_title"])
        if "name" in changed:
            renamed += 1

    Template = env["sale.order.template"].with_context(active_test=False)
    Product = env["odin.cover.product"].with_context(active_test=False)
    for data in env["ir.model.data"].search([("module", "=", MODULE),
                                             ("model", "=", "sale.order.template")]):
        key = next((key for prefix, key in TEMPLATES if data.name.startswith(prefix)), None)
        template = Template.browse(data.res_id).exists()
        if not key or not template:
            continue
        terms = key_terms.html(key)
        products = Product.search([("template_id", "=", template.id)])
        if products:
            products.write({"terms": terms})
        else:
            template.note = terms
        worded += 1
    _logger.info("%s: %s plans and rate tables renamed, key terms on %s plans",
                 MODULE, renamed, worded)
