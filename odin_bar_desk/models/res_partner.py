from odoo import Command, api, fields, models

SPIRITS = [
    "Aperitif", "Blended Whisky", "Cognac & Brandy", "Gin", "Irish, Tennessee & Bourbon",
    "Liqueur", "Malt Whisky", "Rum", "Tequila", "Vodka",
]
WINES = ["Red Wine", "Rosé Wine", "Sparkling Wine", "White Wine"]
BEERS = ["Beer, RTD & Cider"]
# Dhostana's suppliers: what the Desk shows on their tile, and the product
# categories offered first when booking their delivery.
SUPPLIER_SEED = {
    "Khetias": ("Water", ["Water"]),
    "Soys": ("Spirits", SPIRITS),
    "Mega Wines": ("Wines", WINES),
    "Rosso Bianco": ("Wines and spirits", WINES + SPIRITS),
    "Siddham Wines": ("Wines", WINES),
    "Sky City": ("Sodas", ["Sodas"]),
    "Highridge": ("Cigarettes", ["Cigarettes & Matches"]),
    "Benchmark": ("Cigarettes", ["Cigarettes & Matches"]),
    "Muji": ("Snacks", ["Snacks"]),
    "Geeta": ("Snacks", ["Snacks"]),
    "Ishano": ("Beers and spirits", BEERS + SPIRITS),
    "Outlook Index": ("Beers and spirits", BEERS + SPIRITS),
    "Tony West": ("Beers and spirits", BEERS + SPIRITS),
    "Rwathia": ("Beers and spirits", BEERS + SPIRITS),
}


class ResPartner(models.Model):
    _inherit = "res.partner"

    bar_supplies = fields.Char(
        "Supplies",
        help="What this supplier brings, shown on the Bar Desk, e.g. Beers and spirits.",
    )
    bar_supply_categ_ids = fields.Many2many(
        "product.category",
        "res_partner_bar_supply_categ_rel",
        "partner_id",
        "categ_id",
        string="Supplies product categories",
        help="On a supplier delivery, the Bar Desk lists products of these categories first.",
    )

    @api.model
    def _bar_seed_suppliers(self):
        """Fill in what Dhostana's suppliers supply, where still empty."""
        Category = self.env["product.category"].sudo()
        for name, (label, categories) in SUPPLIER_SEED.items():
            for partner in self.sudo().search([("name", "=", name)]):
                vals = {}
                if not partner.bar_supplies:
                    vals["bar_supplies"] = label
                if not partner.bar_supply_categ_ids:
                    found = Category.search([("name", "in", categories)])
                    if found:
                        vals["bar_supply_categ_ids"] = [Command.set(found.ids)]
                if vals:
                    partner.write(vals)
