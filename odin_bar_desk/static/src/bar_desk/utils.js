/** Numbers as staff read them: 3, 2.5, 0.33. */
export function fmt(value) {
    const rounded = Math.round((Number(value) || 0) * 100) / 100;
    return String(rounded);
}

/** Money as staff read it: -2,061, 3,642. */
export function money(value) {
    return Math.round(Number(value) || 0).toLocaleString("en-US");
}

/** "Bottle 750ml" reads as "750ml" next to a number; other units stay as they are. */
export function shortUnit(unit) {
    return unit.name.replace(/^bottle\s+/i, "");
}

function stockUnitLabel(product, qty) {
    const name = product.uom.name;
    if (name.toLowerCase() === "tot") {
        return qty === 1 ? "tot" : "tots";
    }
    return name;
}

/** Units a quantity of this product can be entered in: stock unit, bottles, packs. */
export function unitsOf(product) {
    return [product.uom, ...product.bottles, ...product.packs];
}

export function unitOf(product, uomId) {
    return unitsOf(product).find((unit) => unit.id === uomId) || product.uom;
}

export function qtyLabel(product, qty, uomId) {
    const unit = unitOf(product, uomId);
    if (unit.id === product.uom.id) {
        return `${fmt(qty)} ${stockUnitLabel(product, qty)}`;
    }
    return `${fmt(qty)} × ${shortUnit(unit)}`;
}

/** A quantity on a transfer line: "2 × Bottle 1L", "12 Units". */
export function moveQty(qty, line) {
    return line.pack ? `${fmt(qty)} × ${line.uom_name}` : `${fmt(qty)} ${line.uom_name}`;
}

export function emptyCountLine() {
    return { touched: false, unit_qty: 0, bottle_detail: {}, open_tots: 0 };
}

/** "Crate 25" reads as "cr" next to a number; other packs keep their name. */
export function packShort(pack) {
    const name = shortUnit(pack);
    return name.toLowerCase().startsWith("crate") ? "cr" : name;
}

/** Units in crates, for beer and sodas: "1 cr + 7", "2 cr", "" under one crate. */
function inPacks(product, qty) {
    const pack = product.poured ? null : product.packs[0];
    if (!pack || pack.factor <= 1 || qty < pack.factor) {
        // Under a crate, or below zero: the plain number says it better.
        return "";
    }
    const full = Math.floor(qty / pack.factor + 1e-9);
    const rest = Math.round((qty - full * pack.factor) * 100) / 100;
    return rest ? `${full} ${packShort(pack)} + ${fmt(rest)}` : `${full} ${packShort(pack)}`;
}

/** The unit stock is ordered and compared to par in: bottles for spirits,
 * crates for beer and sodas, units for the rest. */
export function bulkUnit(product) {
    if (product.poured) {
        return { name: "btl", factor: product.bottles[0].factor };
    }
    if (product.packs.length) {
        const pack = product.packs[0];
        return { name: packShort(pack), factor: pack.factor };
    }
    return { name: "", factor: 1 };
}

/** A stock quantity in bulk units, to one decimal: "12 btl", "3.5 cr", "40". */
export function bulkLabel(product, qty) {
    const unit = bulkUnit(product);
    const value = fmt(Math.round((qty / unit.factor) * 10) / 10);
    return unit.name ? `${value} ${unit.name}` : value;
}

/** What a count line says, e.g. "3 btl + 12 tots", "48 (1 cr + 23)". */
export function countLabel(product, line) {
    if (!line?.touched || !product) {
        return "";
    }
    if (!product.poured) {
        return stockLabel(product, line.unit_qty || 0);
    }
    const detail = line.bottle_detail || {};
    const parts = [];
    const sizes = product.bottles.filter((bottle) => detail[bottle.id]);
    if (product.bottles.length === 1 && sizes.length === 1) {
        parts.push(`${fmt(detail[sizes[0].id])} btl`);
    } else {
        for (const bottle of sizes) {
            parts.push(`${fmt(detail[bottle.id])} × ${shortUnit(bottle)}`);
        }
    }
    if (line.open_tots) {
        parts.push(`${fmt(line.open_tots)} ${stockUnitLabel(product, line.open_tots)}`);
    }
    return parts.join(" + ") || "0";
}

/** What a count line adds up to, in the stock unit (tots, units). */
export function countedQty(product, line) {
    if (!line?.touched) {
        return 0;
    }
    if (!product.poured) {
        return line.unit_qty || 0;
    }
    let total = line.open_tots || 0;
    for (const bottle of product.bottles) {
        total += (line.bottle_detail?.[bottle.id] || 0) * bottle.factor;
    }
    return total;
}

/** A count line holding exactly ``qty`` of the stock unit: full bottles of
 * the counting size plus the rest in the open bottle, or plain units. */
export function countLineFor(product, qty) {
    const line = { ...emptyCountLine(), touched: true };
    if (product.poured) {
        const bottle = product.bottles[0];
        const full = Math.floor(qty / bottle.factor + 1e-9);
        line.bottle_detail = full ? { [bottle.id]: full } : {};
        line.open_tots = Math.round((qty - full * bottle.factor) * 100) / 100;
    } else {
        line.unit_qty = qty;
    }
    return line;
}

/** A quantity in the stock unit as staff read it: "182 tots", "48 (1 cr + 23)", "6 pcs". */
export function stockLabel(product, qty, { packs = true } = {}) {
    // The catalogue holds the products on the Desk, and a line can name one it
    // does not: a product added since the Desk loaded, or counted and then
    // taken off "Show in Bar Desk". That is a configuration gap, not a reason
    // to lose the screen -- and losing it is what happened, because one such
    // line took the whole Explain list down with a TypeError. Show the bare
    // quantity instead; the row still names the product and still works.
    const unit = product?.uom?.name;
    if (!unit) {
        return fmt(qty);
    }
    if (unit.toLowerCase() === "tot") {
        return `${fmt(qty)} ${Math.abs(qty) === 1 ? "tot" : "tots"}`;
    }
    const crates = packs && inPacks(product, qty);
    if (crates) {
        return `${fmt(qty)} (${crates})`;
    }
    return `${fmt(qty)} ${unitWord(product)}`;
}

/** "pcs" for plain units, the unit's own name otherwise (kg, L). */
function unitWord(product) {
    const name = product.uom.name;
    return /^units?$/i.test(name) ? "pcs" : name;
}

/** A signed difference: "+3 pcs", "−40 tots". */
export function diffLabel(product, qty) {
    const sign = qty > 0 ? "+" : qty < 0 ? "−" : "";
    return `${sign}${stockLabel(product, Math.abs(qty), { packs: false })}`;
}

/** A count line with one more (or one less) full bottle, or unit. */
export function bumpCountLine(product, line, delta) {
    const next = { ...emptyCountLine(), ...line, touched: true };
    if (product.poured) {
        const bottle = product.bottles[0];
        const detail = { ...(next.bottle_detail || {}) };
        detail[bottle.id] = Math.max(0, (detail[bottle.id] || 0) + delta);
        next.bottle_detail = detail;
    } else {
        next.unit_qty = Math.max(0, (next.unit_qty || 0) + delta);
    }
    return next;
}

export function normalize(text) {
    return (text || "")
        .normalize("NFD")
        .replace(/[̀-ͯ]/g, "")
        .toLowerCase();
}

/** A person as staff read them: an employee named after their login
 * ("kiharekihare@gmail.com") shows as "kiharekihare". */
export function displayName(name) {
    const text = (name || "").trim();
    return /^\S+@\S+$/.test(text) ? text.split("@")[0] : text;
}

/** "Mary Wanjiku" → "Mary": the header has room for one name on a phone. */
export function firstName(name) {
    return displayName(name).split(/\s+/)[0] || "";
}

export function initials(name) {
    return (name || "?")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((word) => word[0].toUpperCase())
        .join("");
}
