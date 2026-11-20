import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * Asked before a customer typed in by name is created, in the sale window:
 * who it may already be. A customer picked here prices the sale at its own
 * pricelist; a new one gets the first pricelist, Retail say. When the KRA
 * PIN typed is already a customer's, it is that taxpayer: one of its
 * branches, or a new branch of it, never a customer of its own.
 */
export class CustomerChoiceDialog extends Component {
    static template = "odin_stock_book.CustomerChoiceDialog";
    static components = { Dialog };
    static props = {
        name: String,
        // Customers with the words of the name, as book_sale_customer_matches finds them.
        matches: Array,
        // The customer the PIN typed is on, and its branches with a word of the name.
        owner: { type: [Object, Boolean], optional: true },
        branches: { type: Array, optional: true },
        pin: { type: String, optional: true },
        newPricelist: { type: String, optional: true },
        choose: Function,
        close: Function,
    };

    get title() {
        return this.props.owner ? _t("A branch of %s?", this.props.owner.display_name) : _t("Already a customer?");
    }

    /** Each customer once: the PIN's branches first, then the PIN's customer, then the others with these words. */
    get customers() {
        const { owner, branches = [], matches } = this.props;
        const seen = new Set();
        const customers = [];
        for (const customer of [...branches, ...(owner ? [owner] : []), ...matches]) {
            if (!seen.has(customer.id)) {
                seen.add(customer.id);
                customers.push(customer);
            }
        }
        return customers;
    }

    hint(customer) {
        const prices = customer.pricelist ? _t("Prices: %s", customer.pricelist) : "";
        return [prices, customer.payment_term].filter(Boolean).join(" · ");
    }

    choose(choice) {
        this.props.choose(choice);
        this.props.close();
    }
}
