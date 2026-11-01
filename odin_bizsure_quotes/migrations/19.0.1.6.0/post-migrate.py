"""The key terms on the plans installed before them: APA Jamii Plus and
Birdview Bizsure Afya.

Their templates are noupdate, so an upgrade does not reach them. The terms are
written on each plan's Cover Quotes product, which puts them on its quotation
template: written on the template alone, they would be put back the next time
someone saved the product. Quotations already made keep the terms they were
sent with.
"""

import importlib.util
import logging
import os

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

#: template external id prefix -> key terms (tools/key_terms.py)
PLANS = (
    ("sot_apa_jamii_plus_child_only_cover", "apa_child_only"),
    ("sot_apa_jamii_plus_family_cover_", "apa_family"),
    ("sot_birdview_bizsure_afya_", "birdview"),
)


def _key_terms():
    path = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "tools", "key_terms.py")
    spec = importlib.util.spec_from_file_location("odin_bizsure_quotes_key_terms", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    key_terms = _key_terms()
    Template = env["sale.order.template"].with_context(active_test=False)
    Product = env["odin.cover.product"].with_context(active_test=False)
    done = 0
    for data in env["ir.model.data"].search([("module", "=", "odin_bizsure_quotes"),
                                             ("model", "=", "sale.order.template")]):
        key = next((key for prefix, key in PLANS if data.name.startswith(prefix)), None)
        template = Template.browse(data.res_id).exists()
        if not key or not template:
            continue
        terms = key_terms.html(key)
        products = Product.search([("template_id", "=", template.id)])
        if products:
            products.write({"terms": terms})
        else:
            template.note = terms
        done += 1
    _logger.info("odin_bizsure_quotes: key terms on %s installed plans", done)
