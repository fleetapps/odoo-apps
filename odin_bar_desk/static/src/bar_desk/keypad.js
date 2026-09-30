import { Component, useState } from "@odoo/owl";
import { fmt, shortUnit, unitsOf } from "./utils";

const MAX_LENGTH = 7;
const MAX_SUM_LENGTH = 40;

/** "30+35+33" adds up to 98; a plain number is itself. */
export function addUp(text) {
    return (text || "")
        .split("+")
        .reduce((total, part) => total + Math.max(0, parseFloat(part) || 0), 0);
}

/**
 * Bottom sheet keypad.
 *
 * - "qty" mode: one number and the unit it is in (tots or a bottle size for
 *   spirits, units or a crate for beer).
 * - "count" mode: full bottles per size plus the open bottle in tots for
 *   spirits; full crates plus loose bottles for beer and sodas; a plain
 *   number for everything else.
 */
export class Keypad extends Component {
    static template = "odin_bar_desk.Keypad";
    static props = {
        product: Object,
        mode: String,
        value: { type: Object, optional: true },
        hint: { type: String, optional: true },
        units: { type: Boolean, optional: true },
        allowRemove: { type: Boolean, optional: true },
        expected: { type: String, optional: true },
        onSame: { type: Function, optional: true },
        onConfirm: Function,
        onRemove: { type: Function, optional: true },
        onClose: Function,
    };
    static defaultProps = { units: true };

    setup() {
        const { product, mode } = this.props;
        const value = this.props.value || {};
        let fields;
        if (mode === "count" && product.poured) {
            const detail = value.bottle_detail || {};
            fields = product.bottles.map((bottle) => ({
                key: `bottle:${bottle.id}`,
                label:
                    product.bottles.length > 1
                        ? `Full bottles · ${shortUnit(bottle)}`
                        : "Full bottles",
                text: detail[bottle.id] ? fmt(detail[bottle.id]) : "",
            }));
            fields.push({
                key: "open",
                label: `Open bottle · ${product.uom.name.toLowerCase() === "tot" ? "tots" : product.uom.name}`,
                text: value.open_tots ? fmt(value.open_tots) : "",
            });
        } else if (mode === "count") {
            // Crates first, then loose bottles: 2 crates of 25 and 3 loose is 53.
            const packs = [...product.packs].sort((a, b) => b.factor - a.factor);
            let rest = value.unit_qty || 0;
            fields = packs.map((pack) => {
                const count = Math.floor(rest / pack.factor + 1e-9);
                rest -= count * pack.factor;
                return {
                    key: `pack:${pack.id}`,
                    label: pack.name,
                    factor: pack.factor,
                    text: count ? fmt(count) : "",
                };
            });
            fields.push({
                key: "units",
                label: packs.length ? `Loose · ${product.uom.name}` : "Count",
                text: rest ? fmt(rest) : "",
            });
        } else {
            fields = [{ key: "qty", label: "Quantity", text: value.qty ? fmt(value.qty) : "" }];
        }
        this.state = useState({
            fields,
            active: 0,
            fresh: true,
            uomId: value.uomId || product.uom.id,
        });
    }

    get units() {
        return this.props.mode === "qty" && this.props.units ? unitsOf(this.props.product) : [];
    }

    get keys() {
        // Counts add up what sits in several places (fridge + shelf + crates): 30+35+33.
        const extra = this.props.mode === "count" ? "+" : ".";
        return ["7", "8", "9", "4", "5", "6", "1", "2", "3", extra, "0", "back"];
    }

    setUnit(unit) {
        this.state.uomId = unit.id;
    }

    focus(index) {
        this.state.active = index;
        this.state.fresh = true;
    }

    press(key) {
        const field = this.state.fields[this.state.active];
        // "+" keeps what is there, to add to it; any other key starts afresh on a new field.
        let text = this.state.fresh && key !== "+" ? "" : field.text;
        if (key === "back") {
            text = text.slice(0, -1);
        } else if (key === "+") {
            text = text && !text.endsWith("+") ? `${text}+` : text;
        } else if (key === ".") {
            text = text.includes(".") ? text : `${text || "0"}.`;
        } else {
            text = text === "0" ? key : text + key;
        }
        field.text = text.slice(0, text.includes("+") ? MAX_SUM_LENGTH : MAX_LENGTH);
        this.state.fresh = false;
    }

    number(key) {
        const field = this.state.fields.find((f) => f.key === key);
        return addUp(field?.text);
    }

    total(field) {
        return field.text.includes("+") ? addUp(field.text) : null;
    }

    confirm() {
        const { product, mode } = this.props;
        if (mode === "qty") {
            this.props.onConfirm({ qty: this.number("qty"), uomId: this.state.uomId });
        } else if (product.poured) {
            const detail = {};
            for (const bottle of product.bottles) {
                const bottles = this.number(`bottle:${bottle.id}`);
                if (bottles) {
                    detail[bottle.id] = bottles;
                }
            }
            this.props.onConfirm({
                touched: true,
                unit_qty: 0,
                bottle_detail: detail,
                open_tots: this.number("open"),
            });
        } else {
            let units = this.number("units");
            for (const pack of product.packs) {
                units += this.number(`pack:${pack.id}`) * pack.factor;
            }
            this.props.onConfirm({
                touched: true,
                unit_qty: units,
                bottle_detail: {},
                open_tots: 0,
            });
        }
    }

    shortUnit(unit) {
        return unit.id === this.props.product.uom.id ? unit.name : shortUnit(unit);
    }
}
