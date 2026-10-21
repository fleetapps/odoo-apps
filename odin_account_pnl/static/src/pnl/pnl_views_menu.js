import { Component, onWillStart, useState } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { useService } from "@web/core/utils/hooks";

/**
 * ★ Views: saved P&L views, the user's own and the ones shared with the
 * team. A view keeps its period as a rule ("last month"), so applying it
 * always shows the right month. "Schedule Send", the button beside the menu,
 * opens the current view's form, where the email is scheduled, or the list of
 * saved views when the page is not one of the user's own.
 */
export class PnlViewsMenu extends Component {
    static template = "odin_account_pnl.PnlViewsMenu";
    static components = { Dropdown, DropdownItem };
    static props = { report: Object };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ views: [], name: "", shared: false });
        // The report's state, observed by this menu too. Read through the
        // report's own proxy, a change of view re-rendered the report only:
        // its `report` prop is the same object, so Owl left this menu alone
        // and the button kept saying "Views".
        this.reportState = useState(this.props.report.state);
        onWillStart(() => this.refresh());
    }

    get report() {
        return this.props.report;
    }

    get current() {
        return this.state.views.find((view) => view.id === this.reportState.viewId);
    }

    async refresh() {
        this.state.views = await this.orm.call("odin.pnl.report", "list_views", []);
    }

    async apply(view) {
        const options = await this.orm.call("odin.pnl.report", "get_view_options", [view.id]);
        if (options) {
            this.report.state.viewId = view.id;
            await this.report.replaceOptions(options);
        }
    }

    async save() {
        const name = this.state.name.trim();
        if (!name) {
            return;
        }
        const result = await this.orm.call("odin.pnl.report", "save_view", [
            this.report.state.options,
            name,
            this.state.shared,
        ]);
        this.state.views = result.views;
        this.state.name = "";
        this.report.state.viewId = result.id;
        this.notification.add(`“${name}” saved.`, { type: "success" });
    }

    async update() {
        const view = this.current;
        const result = await this.orm.call("odin.pnl.report", "save_view", [
            this.report.state.options,
            view.name,
            view.shared,
            view.id,
        ]);
        this.state.views = result.views;
        this.notification.add(`“${view.name}” updated.`, { type: "success" });
    }

    async toggleDefault(view, ev) {
        ev.stopPropagation();
        this.state.views = await this.orm.call("odin.pnl.report", "set_default_view", [
            view.is_default ? false : view.id,
        ]);
    }

    async remove(view, ev) {
        ev.stopPropagation();
        this.state.views = await this.orm.call("odin.pnl.report", "delete_view", [view.id]);
        if (this.report.state.viewId === view.id) {
            this.report.state.viewId = false;
        }
    }

    onNameKey(ev) {
        if (ev.key === "Enter") {
            this.save();
        }
    }

    manage() {
        const view = this.current;
        this.action.doAction(
            view && view.mine
                ? {
                      type: "ir.actions.act_window",
                      name: "Schedule Send",
                      res_model: "odin.pnl.view",
                      res_id: view.id,
                      views: [[false, "form"]],
                      target: "new",
                  }
                : "odin_account_pnl.odin_pnl_view_action",
            { onClose: () => this.refresh() }
        );
    }
}
