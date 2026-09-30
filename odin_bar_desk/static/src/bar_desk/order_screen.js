import { Component, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { errorMessage, feedback, newUuid } from "./desk_model";
import { ItemsEditor } from "./items_editor";

function orderUnit(product) {
    if (product.poured) {
        return product.bottles[0].id;
    }
    return product.packs[product.packs.length - 1]?.id || product.uom.id;
}

/**
 * Order from suppliers. Each supplier's list is filled in from the usual
 * levels, this morning's count and what is already on order: check it,
 * change what you want, Send order.
 */
export class OrderScreen extends Component {
    static template = "odin_bar_desk.OrderScreen";
    static components = { ItemsEditor };
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.orderUnit = orderUnit;
        this.state = useState({
            loading: true,
            data: null,
            supplier: null,
            items: [],
            searching: false,
            query: "",
            suppliers: [],
            sent: null,
            busy: false,
            error: "",
        });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            this.state.data = await this.model.fetch("desk_order_suggestions");
        } catch (error) {
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    open(supplier) {
        Object.assign(this.state, {
            supplier,
            searching: false,
            items: (supplier.lines || []).map((line) => ({
                key: newUuid(),
                productId: line.product_id,
                uomId: line.uom_id,
                qty: line.qty,
            })),
        });
    }

    async startSearch() {
        this.state.searching = true;
        await this.search();
    }

    onQuery() {
        browser.clearTimeout(this.searchTimer);
        this.searchTimer = browser.setTimeout(() => this.search(), 300);
    }

    async search() {
        try {
            this.state.suppliers = await this.model.fetch("desk_suppliers", { query: this.state.query });
        } catch (error) {
            this.state.error = errorMessage(error);
        }
    }

    pickSupplier(partner) {
        const known = this.state.data.suppliers.find((supplier) => supplier.id === partner.id);
        this.open(known || { ...partner, lines: [], ordered: [] });
    }

    backToList() {
        Object.assign(this.state, { supplier: null, items: [], searching: false, sent: null });
    }

    async send() {
        const { supplier, items } = this.state;
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_order_send",
            {
                partner_id: supplier.id,
                lines: items.map((item) => ({ product_id: item.productId, uom_id: item.uomId, qty: item.qty })),
            },
            `Order from ${supplier.name}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        feedback(true);
        if (outcome.status === "queued") {
            this.props.app.toast("No connection: the order is saved and sent automatically.", "warning");
            this.props.app.home();
            return;
        }
        this.state.sent = outcome.result.order;
        this.state.loading = true;
        await this.load();
    }

    async share() {
        const sent = this.state.sent;
        try {
            if (browser.navigator.share) {
                await browser.navigator.share({ title: sent.order, text: sent.text });
            } else {
                await browser.navigator.clipboard.writeText(sent.text);
                this.props.app.toast("Order copied: paste it to the supplier.");
            }
        } catch {
            // Closed the share sheet.
        }
    }
}
