import { Component, onWillStart, useState } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { useService } from "@web/core/utils/hooks";

/**
 * ★ Views: saved P&L views, the user's own and the ones shared with the
 * team. A view keeps its period as a rule ("last month"), so applying it
 * always shows the right month. Scheduling the email is done on the view's
 * form ("Schedule & manage").
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
        onWillStart(() => this.refresh());
    }

    get report() {
        return this.props.report;
    }

    get current() {
        return this.state.views.find((view) => view.id === this.report.state.viewId);
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
