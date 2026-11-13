import { Component, useState } from "@odoo/owl";
import { normalize } from "./utils";

const SEARCH_LIMIT = 60;

/** Full-screen product chooser: most used at this bar, category chips, search. */
export class ProductPicker extends Component {
    static template = "odin_bar_desk.ProductPicker";
    static props = {
        model: Object,
        title: String,
        onPick: Function,
        onClose: Function,
        onlyIds: { type: Object, optional: true },
        markedIds: { type: Object, optional: true },
        hint: { type: Function, optional: true },
        // Offered when nothing has the name searched for: a product the store
        // has never stocked, called with that name and the category on screen.
        onCreate: { type: Function, optional: true },
    };

    setup() {
        this.state = useState({ query: "", categId: "all", showAll: false });
        this.state.categId = this.hasRanking ? "top" : "all";
    }

    get limited() {
        return Boolean(this.props.onlyIds?.size) && !this.state.showAll;
    }

    get allProducts() {
        const products = this.props.model.state.catalog?.products || [];
        return this.limited ? products.filter((p) => this.props.onlyIds.has(p.id)) : products;
    }

    toggleAll() {
        this.state.showAll = !this.state.showAll;
        this.state.categId = "all";
    }

    get hasRanking() {
        return this.allProducts.some((product) => product.rank);
    }

    get categories() {
        const used = new Set(this.allProducts.map((product) => product.categ_id));
        return (this.props.model.state.catalog?.categories || []).filter((c) => used.has(c.id));
    }

    get products() {
        const query = normalize(this.state.query.trim());
        if (query) {
            // A name typed is looked for everywhere: the supplier's own list is
            // for browsing, and "Nothing found" for a product the Desk has
            // invites a duplicate.
            return (this.props.model.state.catalog?.products || [])
                .filter((product) => normalize(product.name).includes(query))
                .slice(0, SEARCH_LIMIT);
        }
        if (this.state.categId === "top") {
            return this.allProducts
                .filter((product) => product.rank)
                .sort((a, b) => a.rank - b.rank);
        }
        if (this.state.categId === "all") {
            return this.allProducts;
        }
        return this.allProducts.filter((product) => product.categ_id === this.state.categId);
    }

    setCategory(categId) {
        this.state.categId = categId;
        this.state.query = "";
    }

    isMarked(product) {
        return this.props.markedIds?.has(product.id);
    }

    /** The name typed, when no product on the Desk has it: one to add. */
    get newName() {
        const name = this.state.query.trim();
        if (!this.props.onCreate || name.length < 3) {
            return "";
        }
        const wanted = normalize(name);
        const known = (this.props.model.state.catalog?.products || []).some(
            (product) => normalize(product.name) === wanted
        );
        return known ? "" : name;
    }

    create() {
        const categId = typeof this.state.categId === "number" ? this.state.categId : false;
        this.props.onCreate(this.newName, categId);
    }
}
