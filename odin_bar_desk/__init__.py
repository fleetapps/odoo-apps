from . import models


def _post_init_hook(env):
    # Bars created before this module was installed get their stock-out reasons.
    for company in env["odin.bar"].search([]).company_id:
        env["odin.bar.reason"]._create_default_reasons(company)
    env["res.partner"]._bar_seed_suppliers()
