/** Numbers as staff read them: 3, 2.5, 0.33. */
export function fmt(value) {
    const rounded = Math.round((Number(value) || 0) * 100) / 100;
    return String(rounded);
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

/** What a count line says, e.g. "3 btl + 12 tots" or "48". */
export function countLabel(product, line) {
    if (!line?.touched) {
        return "";
    }
    if (!product.poured) {
        return fmt(line.unit_qty);
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

export function initials(name) {
    return (name || "?")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((word) => word[0].toUpperCase())
        .join("");
}
