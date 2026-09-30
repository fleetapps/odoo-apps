"""Seed the variance reasons on instances upgraded to the controller flow.

19.0.1.2.0 added `odin.bar.variance.reason` and seeds it from
`_post_init_hook` and on a new location, but an instance that already had the
module installed got neither: its Explain screen had no reasons to tap. The
seeds only fill what is missing, so running them again is harmless.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env["odin.bar"].search([]).company_id:
        env["odin.bar.reason"]._create_default_reasons(company)
        env["odin.bar.variance.reason"]._create_default_reasons(company)
