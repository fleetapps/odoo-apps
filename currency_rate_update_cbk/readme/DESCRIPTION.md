Adds the Central Bank of Kenya as a provider for OCA's *Currency Rate Update*,
so a Kenyan company stops typing exchange rates in by hand.

Odoo Community has no exchange-rate feed at all — `currency_rate_live` is
Enterprise — and OCA's `currency_rate_update` ships only a European Central
Bank provider, whose published list does not quote the shilling. A KES company
therefore fails its own rate update every day. This module closes that.

Rates come from CBK's published monthly series, mapped onto Odoo's convention
of *units of the foreign currency per one unit of company currency*. It handles
the three quirks of the CBK file: the yen is quoted per 100, the East African
currencies (UGX, TZS, RWF, BIF) are quoted per shilling rather than in
shillings, and the header carries CBK's own typos.

A company whose currency is not KES is also supported: CBK quotes everything
against the shilling, so cross-rates are derived from it.
