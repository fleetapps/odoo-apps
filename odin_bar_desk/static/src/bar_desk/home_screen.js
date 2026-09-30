import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";

const REFRESH_DELAY = 60000;

const COUNT_STATES = {
    none: "Not started",
    draft: "In progress",
    submitted: "Submitted",
    recount: "Recount requested",
    approved: "Approved",
};

/** Big tiles for what staff do, and the bar's day so far. */
export class HomeScreen extends Component {
    static template = "odin_bar_desk.HomeScreen";
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        onMounted(() => {
            this.refresh();
            this.timer = browser.setInterval(() => this.refresh(), REFRESH_DELAY);
        });
        onWillUnmount(() => browser.clearInterval(this.timer));
    }

    async refresh() {
        try {
            await this.model.loadHome();
        } catch {
            // Offline: keep showing the last known day.
        }
    }

    get home() {
        return this.desk.home;
    }

    get countLine() {
        const count = this.home?.count;
        if (!count) {
            return "";
        }
        let text = `${count.day_label}: ${COUNT_STATES[count.state] || count.state}`;
        if (count.state === "draft" && count.total) {
            text += ` · ${count.counted}/${count.total}`;
        }
        return text;
    }

    open(screen) {
        this.props.app.go(screen);
    }
}
