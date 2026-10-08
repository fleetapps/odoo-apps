import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { formatAmount } from "./pnl_format";
import { sidePanelRegistry } from "./pnl_registries";

/**
 * The panel beside the report: whatever is opened from a row shows here, so
 * the report stays on screen. Its tabs come from the odin_pnl_side_panels
 * registry; this module adds the entry peek and notes, the AI module adds
 * Explain.
 */
export class PnlSidePanel extends Component {
    static template = "odin_account_pnl.PnlSidePanel";
    static props = { panel: Object, report: Object, close: Function, select: Function };

    get tabs() {
        return sidePanelRegistry
            .getEntries()
            .map(([key, tab]) => ({ key, ...tab }))
            .filter((tab) => !tab.isAvailable || tab.isAvailable(this.props.panel.row))
            .sort((a, b) => (a.sequence || 10) - (b.sequence || 10));
    }

    get current() {
        return this.tabs.find((tab) => tab.key === this.props.panel.tab) || this.tabs[0];
    }
}

/**
 * The source document of a journal item, without leaving the report: its
 * header, lines with analytic split, attachment preview and last notes.
 * ◀ ▶ (or J/K) step through the other items under the same figure.
 */
export class EntryPeek extends Component {
    static template = "odin_account_pnl.EntryPeek";
    static props = { row: Object, report: Object, close: Function };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ data: null, error: "", attachment: null });
        onWillStart(() => this.load(this.props.row));
        onWillUpdateProps((next) => {
            if (next.row.aml_id !== this.props.row.aml_id) {
                return this.load(next.row);
            }
        });
    }

    async load(row) {
        this.state.error = "";
        try {
            const data = await this.orm.call("odin.pnl.report", "get_entry_peek", [row.aml_id]);
            this.state.data = data;
            this.state.attachment = data.attachments.find((a) => this.previewable(a)) || null;
        } catch (error) {
            this.state.error = error.data?.message || error.message || String(error);
        }
    }

    previewable(attachment) {
        return attachment.mimetype === "application/pdf" || attachment.mimetype.startsWith("image/");
    }

    fmt(value) {
        return formatAmount(value, { currencyDecimals: this.props.report.currencyDecimals });
    }

    get siblings() {
        return this.props.report.entrySiblings(this.props.row);
    }

    get position() {
        return this.siblings.findIndex((row) => row.key === this.props.row.key);
    }

    move(delta) {
        this.props.report.peekSibling(this.props.row, delta);
    }

    openMove() {
        this.props.report.openMove(this.props.row.aml_id);
    }

    openNewTab() {
        window.open(`/odoo/account.move/${this.state.data.move_id}`, "_blank");
    }
}

/** Notes on a line or an account, as Enterprise's "Annotate". */
export class AnnotationsPanel extends Component {
    static template = "odin_account_pnl.AnnotationsPanel";
    static props = { row: Object, report: Object, close: Function };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ notes: [], text: "", error: "", dated: true });
        onWillStart(() => this.load(this.props.row));
        onWillUpdateProps((next) => {
            if (next.row.key !== this.props.row.key) {
                return this.load(next.row);
            }
        });
    }

    get canWrite() {
        return this.props.report.state.data?.can_edit_budget;
    }

    async load(row) {
        this.state.notes = await this.orm.call("odin.pnl.report", "get_annotations", [
            this.props.report.state.options,
            row.key,
        ]);
    }

    async add() {
        this.state.error = "";
        try {
            this.state.notes = await this.orm.call("odin.pnl.report", "add_annotation", [
                this.props.report.state.options,
                this.props.row.key,
                this.state.text,
                this.state.dated,
            ]);
            this.state.text = "";
            this.props.report.load();
        } catch (error) {
            this.state.error = error.data?.message || error.message || String(error);
        }
    }

    async remove(note) {
        await this.orm.call("odin.pnl.report", "delete_annotation", [note.id]);
        await this.load(this.props.row);
        this.props.report.load();
    }
}

sidePanelRegistry.add("peek", {
    title: _t("Entry"),
    icon: "fa-file-text-o",
    sequence: 10,
    Component: EntryPeek,
    isAvailable: (row) => Boolean(row.aml_id),
});

sidePanelRegistry.add("notes", {
    title: _t("Notes"),
    icon: "fa-sticky-note-o",
    sequence: 20,
    Component: AnnotationsPanel,
    isAvailable: (row) => ["line", "account"].includes(row.type) && !row.key.includes("/B:"),
});

