import { Component, useState } from "@odoo/owl";
import { newUuid } from "./desk_model";
import { Keypad } from "./keypad";
import { LineList } from "./line_list";
import { NewProductSheet } from "./new_product";
import { ProductPicker } from "./product_picker";
import { money, qtyLabel, shortUnit, unitOf } from "./utils";

// A price this many times the last one, or under its inverse, is questioned
// before the line is kept: most often a zero typed too many or too few.
const PRICE_JUMP = 1.5;

// Products added on this screen have no id until the delivery is posted.
let lastNewId = 0;

/**
 * The list of items a stock out, a delivery to a bar or a receipt is made of:
 * add with the picker, set the quantity and unit on the keypad, ±1 on the line.
 * Items are {key, productId, uomId, qty} in a reactive array owned by the
 * screen, plus {price} when the screen asks for prices (a supplier delivery,
 * where what was paid is what the goods are then valued at).
 *
 * With prices, ``vatMode`` says how the invoice prints them ("added",
 * "included", "none") and ``priceFor`` gives what the supplier last charged
 * before VAT; with ``allowNew`` a name the Desk does not know can be added as
 * a new product.
 */
export class ItemsEditor extends Component {
    static template = "odin_bar_desk.ItemsEditor";
    static components = { Keypad, LineList, NewProductSheet, ProductPicker };
    static props = {
        model: Object,
        items: Array,
        defaultUnit: Function,
        pickerTitle: String,
        onlyIds: { type: Object, optional: true },
        pickerHint: { type: Function, optional: true },
        withPrice: { type: Boolean, optional: true },
        priceFor: { type: Function, optional: true },
        vatMode: { type: String, optional: true },
        allowNew: { type: Boolean, optional: true },
    };

    setup() {
        this.state = useState({ picking: false, keypad: null, newProduct: null });
    }

    /** The screen's own reactive list: changes here re-render the screen. */
    get items() {
        return this.props.items;
    }

    get lines() {
        return this.items.map((item) => {
            const product = this.props.model.product(item.productId);
            const unit = shortUnit(unitOf(product, item.uomId));
            const priced = !this.props.withPrice || item.price;
            let hint;
            if (this.props.withPrice) {
                hint = priced
                    ? `${money(item.price)}/${unit} · ${money(item.qty * item.price)}`
                    : "No price yet: tap to add it";
                if (product.isNew) {
                    hint = `New · ${hint}`;
                }
            }
            return {
                key: item.key,
                item,
                name: product.name,
                label: qtyLabel(product, item.qty, item.uomId),
                hint,
                state: priced ? "entered" : "error",
            };
        });
    }

    get markedIds() {
        return new Set(this.items.map((item) => item.productId));
    }

    /** What the supplier last charged for one ``uomId``, as this invoice
     * prints prices, so the usual case is a confirm. */
    suggestedPrice(product, uomId) {
        const net = (this.props.priceFor && this.props.priceFor(product, uomId)) || 0;
        const gross = this.props.vatMode === "included" ? net * (1 + (product.vat || 0)) : net;
        return Math.round(gross * 100) / 100;
    }

    /** Why a price deserves a second look, or "". */
    priceWarning(product, price, uomId) {
        const unit = unitOf(product, uomId);
        const last = this.suggestedPrice(product, uomId);
        if (last && (price > last * PRICE_JUMP || price < last / PRICE_JUMP)) {
            const ratio = price / last;
            let how = `${Math.round((1 - ratio) * 100)}% less`;
            if (ratio >= 2) {
                how = `${Math.round(ratio * 10) / 10} times that`;
            } else if (ratio > 1) {
                how = `${Math.round((ratio - 1) * 100)}% more`;
            }
            return `Last time it was ${money(last)} a ${shortUnit(unit)}, and ${money(price)} is ${how}. Is that what the invoice says?`;
        }
        if (product.isNew && unit.factor && price / unit.factor > product.salePrice) {
            return `That is more than it sells for (${money(product.salePrice)} a ${product.uom.name.toLowerCase()}). Is the price right?`;
        }
        return "";
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
                price: line.item.price || this.suggestedPrice(product, line.item.uomId),
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

    // ---- a product the Desk does not have yet ---------------------------------

    startNew(name, categId) {
        this.state.picking = false;
        this.state.newProduct = { name, categId };
    }

    /** Stand the new product in for a real one until the delivery is posted:
     * the server creates it then, with the delivery, and only if the delivery
     * goes through. */
    confirmNew({ name, categId, shape, salePrice }) {
        const catalog = this.props.model.state.catalog;
        const product = {
            id: --lastNewId,
            name,
            categ_id: categId,
            uom: shape.uom,
            poured: shape.poured,
            bottles: shape.poured && shape.pack ? [shape.pack] : [],
            packs: !shape.poured && shape.pack ? [shape.pack] : [],
            vat: catalog?.default_vat || 0,
            isNew: true,
            salePrice,
            newProduct: {
                name,
                categ_id: categId,
                uom_id: shape.uom.id,
                pack_uom_id: shape.pack?.id || false,
                sale_price: salePrice,
            },
        };
        this.props.model.addLocalProduct(product);
        this.state.newProduct = null;
        this.pick(product);
    }
}
