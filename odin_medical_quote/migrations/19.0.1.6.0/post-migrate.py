"""The client's email names each plan as the quotation heads it -- insurer,
hospital cover, then product: ``Jubilee Health Insurance – KES 1,000,000
hospital cover (J Care · Premier)`` -- not by its template's name, which
carries the rate card's age variant ("– Below 70").

The email template is noupdate, the broker's to reword, so the plan's name is
replaced only where the template still has the one this module installed, in
every language it is kept in.
"""

import logging

_logger = logging.getLogger(__name__)

OLD = "plan.order_id.sale_order_template_id.name or plan.order_id.name or ''"
NEW = "plan.odin_plan_text or plan.order_id.name or ''"


def migrate(cr, version):
    cr.execute("""
        UPDATE mail_template template
           SET body_html = replace(template.body_html::text, %(old)s, %(new)s)::jsonb
          FROM ir_model_data data
         WHERE data.module = 'odin_medical_quote'
           AND data.name = 'mail_template_medical_quote'
           AND data.model = 'mail.template'
           AND data.res_id = template.id
           AND strpos(template.body_html::text, %(old)s) > 0
    """, {"old": OLD, "new": NEW})
    _logger.info("odin_medical_quote: client email names plans by insurer and cover (%s template)",
                 cr.rowcount)
