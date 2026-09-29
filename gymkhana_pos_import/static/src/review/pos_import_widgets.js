import { Component, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { getDataURLFromFile } from "@web/core/utils/urls";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Screen 1: a drop zone for the Group Sales Register PDF.
 * The server parses and checks the file as soon as it lands (onchange); a
 * problem comes back as one sentence under the zone, a good file goes
 * straight on to the review screen.
 */
export class PosImportDropzone extends Component {
    static template = "gymkhana_pos_import.Dropzone";
    static props = {
        ...standardFieldProps,
        fileNameField: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.input = useRef("input");
        this.state = useState({ dragging: false, busy: false });
    }

    get fileName() {
        return (this.props.fileNameField && this.props.record.data[this.props.fileNameField]) || "";
    }

    onDragOver(ev) {
        ev.preventDefault();
        this.state.dragging = true;
    }

    onDragLeave() {
        this.state.dragging = false;
    }

    async onDrop(ev) {
        ev.preventDefault();
        this.state.dragging = false;
        const file = ev.dataTransfer?.files?.[0];
        if (file) {
            await this.load(file);
        }
    }

    onClick() {
        if (!this.props.readonly && !this.state.busy) {
            this.input.el.click();
        }
    }

    async onFileChange(ev) {
        const file = ev.target.files[0];
        ev.target.value = null;
        if (file) {
            await this.load(file);
        }
    }

    async load(file) {
        if (this.props.readonly) {
            return;
        }
        if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
            this.notification.add(_t("Drop the report as a PDF file."), { type: "danger" });
            return;
        }
        const record = this.props.record;
        this.state.busy = true;
        try {
            const dataUrl = await getDataURLFromFile(file);
            const changes = { [this.props.name]: dataUrl.split(",")[1] };
            if (this.props.fileNameField) {
                changes[this.props.fileNameField] = file.name;
            }
            await record.update(changes);
            if (record.data.parse_error) {
                return;
            }
            if (await record.save()) {
                await this.orm.call(record.resModel, "action_review", [[record.resId]]);
                await record.load();
            }
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("fields").add("pos_import_dropzone", {
    component: PosImportDropzone,
    displayName: _t("PDF drop zone"),
    supportedTypes: ["binary"],
    extractProps: ({ attrs }) => ({ fileNameField: attrs.filename }),
});

/**
 * Screens 2 and 3: the review, rendered from the JSON the server computes.
 * section "summary": header, bar cards, red and orange messages.
 * section "detail": Money tab + one tab per bar, records to be created / created.
 */
export class PosImportReview extends Component {
    static template = "gymkhana_pos_import.Review";
    static props = {
        ...standardFieldProps,
        section: { type: String, optional: true },
    };
    static defaultProps = { section: "summary" };

    setup() {
        this.action = useService("action");
        this.state = useState({ tab: "money" });
    }

    get data() {
        return this.props.record.data[this.props.name] || {};
    }

    get stockTabs() {
        return this.data.stock || [];
    }

    get activeStock() {
        return this.stockTabs.find((tab) => `bar-${tab.bar_id}` === this.state.tab);
    }

    setTab(tab) {
        this.state.tab = tab;
    }

    pricesOf(barId) {
        return (this.data.prices || []).filter((row) => row.bar_id === barId);
    }

    open(link) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: link.model,
            res_id: link.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("fields").add("pos_import_review", {
    component: PosImportReview,
    displayName: _t("POS import review"),
    supportedTypes: ["json"],
    extractProps: ({ options }) => ({ section: options.section || "summary" }),
});
