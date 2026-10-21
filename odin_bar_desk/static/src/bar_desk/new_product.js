import { Component, useState } from "@odoo/owl";
import { normalize, oneUnit, shortUnit } from "./utils";

/**
 * A product the store has never stocked, named on the invoice in hand.
 *
 * Staff pick its category, then how it is kept from the shapes that category
 * already uses -- tots from a 750ml bottle for a gin, crates of 24 for a soda
 * -- and type what it sells for. Nothing about units of measure is asked: the
 * shapes are the ones the Desk can already count.
 */
export class NewProductSheet extends Component {
    static template = "odin_bar_desk.NewProductSheet";
    static props = {
        model: Object,
        name: String,
        categId: { type: [Number, Boolean], optional: true },
        onConfirm: Function,
        onClose: Function,
    };

    setup() {
        this.state = useState({
            name: this.props.name,
            categId: this.props.categId || null,
            shapeKey: null,
            salePrice: "",
            error: "",
        });
        this.state.shapeKey = this.shapes[0]?.key || null;
    }

    get catalog() {
        return this.props.model.state.catalog || { products: [], categories: [] };
    }

    get categories() {
        return this.catalog.categories;
    }

    /** How the products of the chosen category are kept, most used first. */
    get shapes() {
        const shapes = new Map();
        // ``weight`` 0 offers a shape without letting it outrank the ones
        // in use: beer comes in crates even though a crate is optional.
        const add = (product, pack, label, weight = 1) => {
            const key = `${product.uom.id}:${pack?.id || 0}`;
            const shape = shapes.get(key) || { key, uom: product.uom, pack, poured: product.poured, label, count: 0 };
            shape.count += weight;
            shapes.set(key, shape);
        };
        for (const product of this.catalog.products) {
            if (product.categ_id !== this.state.categId) {
                continue;
            }
            if (product.poured) {
                const bottle = product.bottles[0];
                add(product, bottle, `Tots · ${shortUnit(bottle)} bottle`);
            } else if (product.packs.length) {
                add(product, product.packs[0], `${product.uom.name} · ${product.packs[0].name}`);
                add(product, null, `${product.uom.name}, no crate or pack`, 0);
            } else {
                add(product, null, product.uom.name);
            }
        }
        return [...shapes.values()].sort((a, b) => b.count - a.count);
    }

    get shape() {
        return this.shapes.find((shape) => shape.key === this.state.shapeKey);
    }

    /** "per tot", "per unit": what the selling price is for. */
    get perUnit() {
        return this.shape ? oneUnit(this.shape.uom).toLowerCase() : "unit";
    }

    setCategory(categId) {
        this.state.categId = categId;
        this.state.shapeKey = this.shapes[0]?.key || null;
        this.state.error = "";
    }

    confirm() {
        const name = this.state.name.trim().replace(/\s+/g, " ");
        const salePrice = parseFloat(String(this.state.salePrice).replace(/,/g, "")) || 0;
        const same = this.catalog.products.find((product) => normalize(product.name) === normalize(name));
        if (!name) {
            this.state.error = "Type the product's name.";
        } else if (same) {
            this.state.error = `${same.name} is already on the Desk: pick it from the list instead.`;
        } else if (!this.state.categId) {
            this.state.error = "Pick its category.";
        } else if (!this.shape) {
            this.state.error = "Pick how it is kept.";
        } else if (salePrice <= 0) {
            this.state.error = `Type what it sells for, per ${this.perUnit}.`;
        } else {
            this.props.onConfirm({ name, categId: this.state.categId, shape: this.shape, salePrice });
        }
    }
}
