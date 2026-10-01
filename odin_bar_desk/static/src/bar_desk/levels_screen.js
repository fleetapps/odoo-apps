import { Component, onWillStart, useState } from "@odoo/owl";
import { errorMessage } from "./desk_model";
import { bulkUnit, fmt, normalize } from "./utils";

/**
 * Stock at every location and in total, in bottles and crates, against the
 * reorder level (par). Replaces the TOTALS and BULK AND REORDER sheets.
 */
export class LevelsScreen extends Component {
    static template = "odin_bar_desk.LevelsScreen";
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.state = useState({ loading: true, data: null, filter: "all", query: "", error: "" });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            this.state.data = await this.model.fetch("desk_stock_levels");
        } catch (error) {
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    /** ``whole`` rounds up to full bottles or crates: a par or a reorder is
     * never "69.6 btl" (pars set in another bottle size land between two). */
    bulk(value, unit, whole = false) {
        const qty = whole ? Math.ceil(value / unit.factor - 1e-9) : Math.round((value / unit.factor) * 10) / 10;
        return unit.name ? `${fmt(qty)} ${unit.name}` : fmt(qty);
    }

    get rows() {
        if (!this.state.data) {
            return [];
        }
        const query = normalize(this.state.query.trim());
        const rows = [];
        for (const row of this.state.data.rows) {
            const product = this.model.product(row.product_id);
            if (!product || (query && !normalize(product.name).includes(query))) {
                continue;
            }
            const unit = bulkUnit(product);
            const below = row.par > 0 && row.total < row.par;
            if (this.state.filter === "below" && !below) {
                continue;
            }
            rows.push({
                key: row.product_id,
                name: product.name,
                total: this.bulk(row.total, unit),
                par: row.par ? this.bulk(row.par, unit, true) : "",
                reorder: below ? this.bulk(row.par - row.total, unit, true) : "",
                below,
                negative: row.total < 0,
                locations: this.state.data.locations
                    .map((location) => ({
                        code: location.code,
                        qty: row.qty[location.id] || 0,
                    }))
                    .filter((location) => location.qty)
                    .map((location) => `${location.code} ${this.bulk(location.qty, unit)}`)
                    .join(" · "),
            });
        }
        return rows;
    }

    get belowCount() {
        return (this.state.data?.rows || []).filter((row) => row.par > 0 && row.total < row.par).length;
    }
}
