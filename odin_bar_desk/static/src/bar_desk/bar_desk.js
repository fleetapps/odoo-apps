import { Component, onWillStart, onWillUnmount, useExternalListener, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { CountScreen } from "./count_screen";
import { DeskModel } from "./desk_model";
import { DifferencesScreen } from "./differences_screen";
import { HomeScreen } from "./home_screen";
import { LevelsScreen } from "./levels_screen";
import { LockScreen } from "./lock_screen";
import { MoveScreen } from "./move_screen";
import { StoreReceiveScreen } from "./store_screens";
import { firstName, initials } from "./utils";

/** Back to the PIN screen after this long without a touch, so a shared
 * tablet left open does not keep booking to the last person. */
const IDLE_LOCK_DELAY = 10 * 60 * 1000;
const TOAST_DELAY = 3500;

const SCREENS = {
    home: { component: HomeScreen, title: "Bar Desk" },
    move: { component: MoveScreen, title: "Log a move" },
    count: { component: CountScreen, title: "Count" },
    differences: { component: DifferencesScreen, title: "Differences" },
    levels: { component: LevelsScreen, title: "Stock levels" },
    receive: { component: StoreReceiveScreen, title: "Supplier delivery" },
};

export class BarDesk extends Component {
    static template = "odin_bar_desk.BarDesk";
    static components = { LockScreen };
    static props = { ...standardActionServiceProps };

    setup() {
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.model = new DeskModel(useService("orm"), (message, type = "info", sticky = false) =>
            this.notification.add(message, { type, sticky })
        );
        this.desk = useState(this.model.state);
        this.nav = useState({ stack: [{ name: "home", params: {} }] });
        this.toastState = useState({ message: "", type: "success" });
        this.initials = initials;
        this.firstName = firstName;
        // Opened from the dashboard on a given trading day.
        const day = this.props.action?.context?.bar_desk_day;
        if (day) {
            this.model.state.day = day;
        }
        onWillStart(() => this.model.boot());
        onWillUnmount(() => {
            this.model.destroy();
            browser.clearTimeout(this.idleTimer);
            browser.clearTimeout(this.toastTimer);
        });
        useExternalListener(window, "pointerdown", () => this.touch(), { capture: true });
        useExternalListener(window, "keydown", () => this.touch(), { capture: true });
        this.touch();
    }

    get screen() {
        return this.nav.stack[this.nav.stack.length - 1];
    }

    get screenComponent() {
        return SCREENS[this.screen.name].component;
    }

    get title() {
        return this.screen.params?.title || SCREENS[this.screen.name].title;
    }

    get subtitle() {
        const day = this.desk.home?.day;
        if (!day) {
            return "";
        }
        if (this.screen.name === "levels" || this.screen.name === "receive") {
            return "Now";
        }
        return `Closing ${day.label}`;
    }

    go(name, params = {}) {
        this.nav.stack.push({ name, params });
    }

    /** Swap the current screen for another, e.g. a finished count for its differences. */
    replace(name, params = {}) {
        this.nav.stack.splice(this.nav.stack.length - 1, 1, { name, params });
    }

    back() {
        if (this.nav.stack.length > 1) {
            this.nav.stack.pop();
        }
    }

    home() {
        this.nav.stack.splice(1);
    }

    toast(message, type = "success") {
        Object.assign(this.toastState, { message, type });
        browser.clearTimeout(this.toastTimer);
        this.toastTimer = browser.setTimeout(() => (this.toastState.message = ""), TOAST_DELAY);
    }

    confirm(title, body, confirmLabel) {
        return new Promise((resolve) => {
            this.dialog.add(
                ConfirmationDialog,
                {
                    title,
                    body,
                    confirmLabel,
                    confirm: () => resolve(true),
                    cancel: () => resolve(false),
                },
                { onClose: () => resolve(false) }
            );
        });
    }

    touch() {
        browser.clearTimeout(this.idleTimer);
        this.idleTimer = browser.setTimeout(() => {
            if (this.desk.token) {
                this.model.lock();
                this.home();
            }
        }, IDLE_LOCK_DELAY);
    }

    switchStaff() {
        this.model.lock();
        this.home();
    }

    flush() {
        this.model.flushOutbox();
    }
}

registry.category("actions").add("bar_desk", BarDesk);
