import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

/**
 * The email of a scheduled view as its recipients would get it now: who it
 * is from, to whom, its subject, attachments and body. The body comes from
 * odin.pnl.view.get_email_preview, computed as the view's owner; nothing is
 * sent. Shown in a sandboxed frame so the email's own styles stay its own.
 */
export class PnlEmailPreviewDialog extends Component {
    static template = "odin_account_pnl.EmailPreviewDialog";
    static components = { Dialog };
    static props = { close: Function, preview: Object };
}

/**
 * "Preview the email", in the Schedule dialog (a view widget,
 * web/static/src/views/widgets). The record is saved first, so the preview
 * shows what was typed; a schedule that cannot be saved yet says why where
 * the form always does.
 */
export class PnlEmailPreviewButton extends Component {
    static template = "odin_account_pnl.EmailPreviewButton";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.state = useState({ loading: false });
    }

    async open() {
        if (this.state.loading) {
            return;
        }
        this.state.loading = true;
        try {
            const record = this.props.record;
            if (!(await record.save())) {
                return;
            }
            const preview = await this.orm.call("odin.pnl.view", "get_email_preview", [[record.resId]]);
            this.dialog.add(PnlEmailPreviewDialog, { preview });
        } finally {
            this.state.loading = false;
        }
    }
}

registry.category("view_widgets").add("odin_pnl_email_preview", {
    component: PnlEmailPreviewButton,
});
