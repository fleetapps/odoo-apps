"""The catalogue becomes the office's: rates are kept in Cover Quotes >
Products from now on, so an upgrade must leave installed products and plans
alone. The data files say noupdate too; this marks the records already
installed, which loading a file does not change."""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_model_data
           SET noupdate = TRUE
         WHERE module = 'odin_bizsure_quotes'
           AND model IN ('product.product', 'product.template',
                         'sale.order.template', 'sale.order.template.line')
    """)
