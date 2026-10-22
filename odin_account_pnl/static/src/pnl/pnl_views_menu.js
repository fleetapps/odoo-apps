import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { useService } from "@web/core/utils/hooks";

/**
 * What a saved view stands for, to tell whether the page still shows it:
 * everything but the rows unfolded by hand, which come and go as one reads.
 * The server returns options in one key order, so their JSON compares.
 */
export function viewSignature(options) {
    const { unfolded, modes, ...rest } = options || {};
    return JSON.stringify(rest);
}

/**
 * ★ Views: saved P&L views, the user's own and the ones shared with the
 * team. A view keeps its period as a rule ("last month"), so applying it
 * always shows the right month. The button names the view on the page and
 * marks it once the page has changed since the view was saved; the menu then
 * offers to save the change or go back to the view as saved.
 *
 * "Schedule Send", the button beside the menu, opens the current view's
 * schedule; on a page that is not one of the user's own views it opens a new
 * scheduled view made of the page as it is.
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

    /** The page no longer shows the view as saved. */
    get changed() {
        const state = this.reportState;
        return Boolean(state.viewId && state.viewSnapshot) && viewSignature(state.options) !== state.viewSnapshot;
    }

    /** The user's own views, then the ones shared with them. */
    get viewSections() {
        return [
            { title: _t("My views"), views: this.state.views.filter((view) => view.mine) },
            { title: _t("Shared with me"), views: this.state.views.filter((view) => !view.mine) },
        ].filter((section) => section.views.length);
    }

    get canSchedule() {
        return Boolean(this.reportState.data?.can_schedule);
    }

    async refresh() {
        this.state.views = await this.orm.call("odin.pnl.report", "list_views", []);
    }

    async apply(view) {
        const options = await this.orm.call("odin.pnl.report", "get_view_options", [view.id]);
        if (options) {
            this.report.state.viewId = view.id;
            await this.report.replaceOptions(options);
            this.report.markViewSaved();
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
        this.report.markViewSaved();
        this.notification.add(_t("“%s” saved.", name), { type: "success" });
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
        this.report.markViewSaved();
        this.notification.add(_t("“%s” updated.", view.name), { type: "success" });
    }

    /** Back to the view as saved, dropping the changes made since. */
    async revert() {
        if (this.current) {
            await this.apply(this.current);
        }
    }

    async resetView() {
        await this.report.resetView();
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
            this.report.state.viewSnapshot = null;
        }
    }

    onNameKey(ev) {
        if (ev.key === "Enter") {
            this.save();
        }
    }

    scheduleSend() {
        const view = this.current;
        if (view && view.mine) {
            if (this.changed) {
                this.notification.add(
                    _t("The email sends “%s” as saved. Update the view to send the page as it is now.", view.name),
                    { type: "warning" }
                );
            }
            this.action.doAction(
                {
                    type: "ir.actions.act_window",
                    name: _t("Schedule Send"),
                    res_model: "odin.pnl.view",
                    res_id: view.id,
                    views: [[false, "form"]],
                    target: "new",
                },
                { onClose: () => this.refresh() }
            );
            return;
        }
        // Not one of the user's views: schedule the page as it is, as a new
        // view of their own (a shared view is copied, never changed).
        if (this.reportState.options.date.filter === "custom") {
            this.notification.add(
                _t("This page shows fixed dates, so every email would send these same dates. " +
                    "Pick a period that moves with the calendar (Last month, This quarter…) first."),
                { type: "warning", sticky: true }
            );
        }
        this.action.doAction(
            {
                type: "ir.actions.act_window",
                name: _t("Schedule Send"),
                res_model: "odin.pnl.view",
                views: [[false, "form"]],
                target: "new",
                context: {
                    default_name: view ? view.name : _t("Profit & Loss"),
                    default_options: JSON.parse(JSON.stringify(this.reportState.options)),
                    default_schedule: "monthly",
                },
            },
            { onClose: () => this.refresh() }
        );
    }
}
