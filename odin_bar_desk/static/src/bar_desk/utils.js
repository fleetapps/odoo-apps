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

/** Units in crates, for beer and sodas: "1 cr + 7", "2 cr", "" under a crate. */
function inPacks(product, qty) {
    const pack = product.poured ? null : product.packs[0];
    if (!pack || pack.factor <= 1 || Math.abs(qty) < pack.factor) {
        return "";
    }
    const full = Math.floor(Math.abs(qty) / pack.factor + 1e-9);
    const rest = Math.round((Math.abs(qty) - full * pack.factor) * 100) / 100;
    return rest ? `${full} ${packShort(pack)} + ${fmt(rest)}` : `${full} ${packShort(pack)}`;
}

/** What a count line says, e.g. "3 btl + 12 tots", "48 (1 cr + 23)". */
export function countLabel(product, line) {
    if (!line?.touched) {
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
    if (product.uom.name.toLowerCase() === "tot") {
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

/** "Mary Wanjiku" → "Mary": the header has room for one name on a phone. */
export function firstName(name) {
    return (name || "").trim().split(/\s+/)[0] || "";
}

export function initials(name) {
    return (name || "?")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((word) => word[0].toUpperCase())
        .join("");
}
