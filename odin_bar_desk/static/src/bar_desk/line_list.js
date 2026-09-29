import { Component } from "@odoo/owl";

/**
 * One list of big lines. The colour says the state:
 * untouched (grey), entered (green), changed (orange), error (red).
 * Tap a line to open the keypad; ±1 sits at the line's edges.
 */
export class LineList extends Component {
    static template = "odin_bar_desk.LineList";
    static props = {
        lines: Array,
        onTap: Function,
        onBump: { type: Function, optional: true },
        empty: { type: String, optional: true },
    };

    /** Lines grouped under their headers, so a header only sticks while its
     * own lines are on screen. */
    get sections() {
        const sections = [];
        for (const line of this.props.lines) {
            if (line.header || !sections.length) {
                sections.push({ key: line.key, header: line.header || "", lines: [] });
            }
            sections[sections.length - 1].lines.push(line);
        }
        return sections;
    }
}
