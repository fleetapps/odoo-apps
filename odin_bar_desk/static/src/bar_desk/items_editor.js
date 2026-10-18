import { Component, useState } from "@odoo/owl";
import { newUuid } from "./desk_model";
import { Keypad } from "./keypad";
import { LineList } from "./line_list";
import { ProductPicker } from "./product_picker";
import { money, qtyLabel, shortUnit, unitOf } from "./utils";

/**
 * The list of items a stock out, a delivery to a bar or a receipt is made of:
 * add with the picker, set the quantity and unit on the keypad, ±1 on the line.
 * Items are {key, productId, uomId, qty} in a reactive array owned by the
 * screen, plus {price} when the screen asks for prices (a supplier delivery,
 * where what was paid is what the goods are then valued at).
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
        withPrice: { type: Boolean, optional: true },
        priceFor: { type: Function, optional: true },
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
                hint: this.props.withPrice
                    ? `${money(item.price)}/${shortUnit(unitOf(product, item.uomId))} · ${money(item.qty * item.price)}`
                    : undefined,
                state: "entered",
            };
        });
    }

    get markedIds() {
        return new Set(this.items.map((item) => item.productId));
    }

    /** What the supplier last charged, so the usual case is a confirm. */
    suggestedPrice(product, uomId) {
        return this.props.priceFor ? this.props.priceFor(product, uomId) : 0;
    }

    pick(product) {
        this.state.picking = false;
        const uomId = this.props.defaultUnit(product);
        this.state.keypad = {
            product,
            item: null,
            value: { qty: 0, uomId, price: this.suggestedPrice(product, uomId) },
        };
    }

    tap(line) {
        const product = this.props.model.product(line.item.productId);
        this.state.keypad = {
            product,
            item: line.item,
            value: {
                qty: line.item.qty,
                uomId: line.item.uomId,
                price: line.item.price ?? this.suggestedPrice(product, line.item.uomId),
            },
        };
    }

    bump(line, delta) {
        line.item.qty = Math.max(0, line.item.qty + delta);
        if (!line.item.qty) {
            this.removeItem(line.item);
        }
    }

    confirm({ qty, uomId, price }) {
        const { product, item } = this.state.keypad;
        if (item) {
            if (qty) {
                item.qty = qty;
                item.uomId = uomId;
                if (price !== undefined) {
                    item.price = price;
                }
            } else {
                this.removeItem(item);
            }
        } else if (qty) {
            const same = this.items.find((i) => i.productId === product.id && i.uomId === uomId);
            if (same) {
                same.qty += qty;
                // The price just typed is the one on the invoice in hand.
                if (price !== undefined) {
                    same.price = price;
                }
            } else {
                this.items.push({ key: newUuid(), productId: product.id, uomId, qty, price: price ?? 0 });
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
