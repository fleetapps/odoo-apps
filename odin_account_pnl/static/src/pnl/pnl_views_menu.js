import { Component, onWillStart, useState } from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
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
    const rest = { ...(options || {}) };
    delete rest.unfolded;
    delete rest.modes;
    return JSON.stringify(rest);
}

/**
 * ★ Views: saved P&L views, the user's own and the ones shared with the
 * team. A view keeps its period as a rule ("last month"), so applying it
 * always shows the right month. The button names the view on the page and
 * marks it once the page has changed since the view was saved; the menu then
 * offers to save the change or go back to the view as saved.
 *
 * "Schedule", the button beside the menu, opens the current view's schedule
 * (odin.pnl.report.action_schedule); on a page that is not one of the user's
 * own views it opens a new scheduled view made of the page as it is. The
 * button says when the view on screen is emailed, paused or failing.
 */
export class PnlViewsMenu extends Component {
    static template = "odin_account_pnl.PnlViewsMenu";
    static components = { Dropdown, DropdownItem };
    static props = { report: Object };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
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

    /** The schedule of the view on screen, when it is the user's and emailed. */
    get schedule() {
        const view = this.current;
        if (!view || !view.mine || !view.schedule) {
            return null;
        }
        return { summary: view.schedule, paused: view.paused, failed: view.failed, status: view.status };
    }

    get scheduleTooltip() {
        const schedule = this.schedule;
        if (!schedule) {
            return this.current?.mine
                ? _t("Email “%s” on a schedule", this.current.name)
                : _t("Email this page on a schedule");
        }
        return schedule.failed ? `${schedule.summary}. ${schedule.status}` : schedule.summary;
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

    remove(view, ev) {
        ev.stopPropagation();
        this.dialog.add(ConfirmationDialog, {
            title: _t("Delete “%s”?", view.name),
            body: [
                view.schedule ? _t("Its emails stop (%s).", view.schedule) : "",
                view.shared ? _t("Everyone it is shared with loses it too.") : "",
                _t("This cannot be undone."),
            ].filter(Boolean).join(" "),
            confirmLabel: _t("Delete"),
            confirmClass: "btn-danger",
            confirm: async () => {
                this.state.views = await this.orm.call("odin.pnl.report", "delete_view", [view.id]);
                if (this.report.state.viewId === view.id) {
                    this.report.state.viewId = false;
                    this.report.state.viewSnapshot = null;
                }
            },
            cancel: () => {},
        });
    }

    onNameKey(ev) {
        if (ev.key === "Enter") {
            this.save();
        }
    }

    async scheduleSend() {
        const view = this.current;
        if (view && view.mine && this.changed) {
            this.notification.add(
                _t("The email sends “%s” as saved. Update the view to send the page as it is now.", view.name),
                { type: "warning" }
            );
        }
        // The dialog: the view's own schedule, or a new view of the page as
        // it is (fixed dates are flagged in the dialog itself).
        const action = await this.orm.call("odin.pnl.report", "action_schedule", [
            JSON.parse(JSON.stringify(this.reportState.options)),
            view ? view.id : false,
        ]);
        const before = new Set(this.state.views.map((item) => item.id));
        await this.action.doAction(action, {
            onClose: async () => {
                await this.refresh();
                // Saved from the page as it is: the new view is the one on
                // screen now, so the button names it and shows its schedule.
                const created = this.state.views.filter((item) => item.mine && !before.has(item.id));
                if (created.length === 1) {
                    this.report.state.viewId = created[0].id;
                    this.report.markViewSaved();
                }
            },
        });
    }
}
