from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockBookAssets(TransactionCase):
    """A style Odoo's compiler refuses stops the whole database for everyone,
    and no other test here would notice: they never compile the styles."""

    def test_styles_compile_in_light_and_dark(self):
        for name in ("web.assets_web", "web.assets_web_dark"):
            bundle = self.env["ir.qweb"]._get_asset_bundle(name, js=False)
            bundle.preprocess_css()
            self.assertFalse(bundle.css_errors, f"{name} does not compile")
