"""The page's styles compile: a Sass error does not fail a click in a test,
but it leaves every user of the backend with Odoo's error banner and no
styles from the bundle."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAssets(TransactionCase):

    def test_the_backend_styles_compile(self):
        bundle = self.env["ir.qweb"]._get_asset_bundle("web.assets_backend", js=False)
        self.assertTrue(any("odin_account_pnl" in (asset.url or "") for asset in bundle.stylesheets),
                        "the P&L's stylesheet is in the bundle")
        bundle.preprocess_css()
        self.assertEqual(bundle.css_errors, [])
