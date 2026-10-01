{
    'name': 'Gymkhana POS Day Import',
    'version': '19.0.1.2.0',
    'category': 'Sales/Sales',
    'summary': "Post a day of Nairobi Gymkhana bar sales from the POS "
               "Group Sales Register PDF: sale orders, bar deliveries and one invoice.",
    'description': """
Drop the day's Group Sales Register PDF, review it on one screen, press
"Post day". The import creates one sale order and one delivery per bar
(stock deducted from the bar's own location, kits exploded into their
components) and one consolidated invoice whose total equals the PDF's
grand total to the cent. Posting is all-or-nothing, and a posted day can
be undone while its invoice is unpaid.

The bars are those of Bar Control (odin_bar_base). Each posted day is
recorded there per bar, with the trading day on its deliveries, so the Bar
Desk counts know which sales are in.
""",
    'author': 'Fleet',
    'license': 'GPL-3',
    'depends': ['sale_stock', 'sale_mrp', 'odin_bar_base'],
    'external_dependencies': {'python': ['pdfplumber']},
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/product_data.xml',
        'views/pos_import_views.xml',
        'views/odin_bar_views.xml',
        'views/pos_item_map_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'gymkhana_pos_import/static/src/review/*',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
}
