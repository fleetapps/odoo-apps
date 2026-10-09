import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * "?" (or the keyboard button) on the P&L: the keyboard shortcuts and the
 * clicks that are easy to miss. Line actions that declare a key in the
 * odin_pnl_line_actions registry are listed too. Opened through the dialog
 * service, on Odoo's Dialog (@web/core/dialog/dialog).
 */
export class PnlShortcutsDialog extends Component {
    static template = "odin_account_pnl.PnlShortcutsDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        lineActions: { type: Array, optional: true },
    };
    static defaultProps = { lineActions: [] };

    get shortcuts() {
        return [
            { keys: ["↑", "↓"], label: _t("Move between lines") },
            { keys: ["→"], label: _t("Unfold the line") },
            { keys: ["←"], label: _t("Fold the line, or go to the line it belongs to") },
            { keys: ["Enter"], label: _t("Open the line, or show the journal item beside the report") },
            { keys: ["J", "K"], label: _t("Next / previous journal item in the side panel") },
            { keys: ["B"], label: _t("Split the line by partner, product, month…") },
            { keys: ["/"], label: _t("Find an account") },
            { keys: ["Esc"], label: _t("Close the menu, then the side panel") },
            ...this.props.lineActions.map((action) => ({ keys: [action.key.toUpperCase()], label: action.label })),
            { keys: ["?"], label: _t("Show these shortcuts") },
        ];
    }
}
