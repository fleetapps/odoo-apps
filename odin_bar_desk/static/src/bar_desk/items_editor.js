import { Component, useState } from "@odoo/owl";
import { newUuid } from "./desk_model";
import { Keypad } from "./keypad";
import { LineList } from "./line_list";
import { ProductPicker } from "./product_picker";
import { qtyLabel } from "./utils";

/**
 * The list of items a stock out, a delivery to a bar or a receipt is made of:
 * add with the picker, set the quantity and unit on the keypad, ±1 on the line.
 * Items are {key, productId, uomId, qty} in a reactive array owned by the screen.
 */
export class ItemsEditor extends Component {
    static template = "odin_bar_desk.ItemsEditor";
    static components = { Keypad, LineList, ProductPicker };
    static props = {
        model: Object,
        items: Array,
        defaultUnit: Function,
        pickerTitle: String,
        onlyIds: { type: Object, optional: true },
        pickerHint: { type: Function, optional: true },
    };

    setup() {
        this.state = useState({ picking: false, keypad: null });
    }

    /** The screen's own reactive list: changes here re-render the screen. */
    get items() {
        return this.props.items;
    }

    get lines() {
        return this.items.map((item) => {
            const product = this.props.model.product(item.productId);
            return {
                key: item.key,
                item,
                name: product.name,
                label: qtyLabel(product, item.qty, item.uomId),
                state: "entered",
            };
        });
    }

    get markedIds() {
        return new Set(this.items.map((item) => item.productId));
    }

    pick(product) {
        this.state.picking = false;
        this.state.keypad = {
            product,
            item: null,
            value: { qty: 0, uomId: this.props.defaultUnit(product) },
        };
    }

    tap(line) {
        this.state.keypad = {
            product: this.props.model.product(line.item.productId),
            item: line.item,
            value: { qty: line.item.qty, uomId: line.item.uomId },
        };
    }

    bump(line, delta) {
        line.item.qty = Math.max(0, line.item.qty + delta);
        if (!line.item.qty) {
            this.removeItem(line.item);
        }
    }

    confirm({ qty, uomId }) {
        const { product, item } = this.state.keypad;
        if (item) {
            if (qty) {
                item.qty = qty;
                item.uomId = uomId;
            } else {
                this.removeItem(item);
            }
        } else if (qty) {
            const same = this.items.find((i) => i.productId === product.id && i.uomId === uomId);
            if (same) {
                same.qty += qty;
            } else {
                this.items.push({ key: newUuid(), productId: product.id, uomId, qty });
            }
        }
        this.state.keypad = null;
    }

    removeItem(item) {
        const index = this.items.indexOf(item);
        if (index >= 0) {
            this.items.splice(index, 1);
        }
    }

    removeCurrent() {
        if (this.state.keypad?.item) {
            this.removeItem(this.state.keypad.item);
        }
        this.state.keypad = null;
    }
}
