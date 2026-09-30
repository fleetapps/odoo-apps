import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { useService } from "@web/core/utils/hooks";
import { errorMessage, feedback } from "./desk_model";

const REFRESH_DELAY = 60000;

function plural(count, word) {
    return `${count} ${word}${count === 1 ? "" : "s"}`;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const COUNT_STATES = {
    none: "Not counted",
    draft: "Counting",
    submitted: "Counted",
    recount: "Recount asked",
    approved: "Approved",
};

/**
 * The day sheet: one trading day, closed the next morning in five steps.
 * POS sales in, moves logged, every location counted, every difference
 * explained, then the day approved. Each step says whether it is done.
 */
export class HomeScreen extends Component {
    static template = "odin_bar_desk.HomeScreen";
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        this.action = useService("action");
        this.state = useState({ allMoves: false, busy: false, error: "" });
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

    /** "Today", else the weekday: the top line of a day chip. */
    dayName(day) {
        return day.date === this.home.days[this.home.days.length - 1].date
            ? "Today"
            : WEEKDAYS[this.date(day).getDay()];
    }

    /** "29 Sep": the bottom line of a day chip. */
    dayDate(day) {
        const date = this.date(day);
        return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
    }

    date(day) {
        const [year, month, date] = day.date.split("-").map(Number);
        return new Date(year, month - 1, date);
    }

    get anyCounted() {
        return this.home.locations.some((location) => ["submitted", "recount", "approved"].includes(location.state));
    }

    get home() {
        return this.desk.home;
    }

    get posLocations() {
        return this.home.locations.filter((location) => location.pos !== "none");
    }

    get posDone() {
        return this.posLocations.every((location) => location.pos === "posted");
    }

    get countsDone() {
        return this.home.counted === this.home.locations.length;
    }

    get moves() {
        return this.state.allMoves ? this.home.moves : this.home.moves.slice(0, 3);
    }

    countLine(location) {
        if (location.state === "draft") {
            return location.total ? `Counting · ${location.counted}/${location.total}` : "Counting";
        }
        if (location.state === "submitted" || location.state === "recount") {
            if (!location.differences) {
                return "Counted · matches";
            }
            return location.unresolved
                ? `${location.unresolved} of ${location.differences} to explain`
                : `${location.differences} explained`;
        }
        if (location.state === "approved") {
            return location.differences ? `Approved · ${plural(location.differences, "difference")}` : "Approved";
        }
        return COUNT_STATES[location.state] || location.state;
    }

    countTone(location) {
        if (location.state === "approved") {
            return "o_done";
        }
        if (location.state === "submitted" || location.state === "recount") {
            return location.unresolved ? "o_attention" : "o_done";
        }
        return location.state === "draft" ? "o_progress" : "";
    }

    async setDay(date) {
        if (date === this.home.day.date) {
            return;
        }
        this.state.error = "";
        try {
            await this.model.setDay(date);
        } catch (error) {
            this.state.error = errorMessage(error);
        }
    }

    openLocation(location) {
        if (location.state === "approved") {
            return;
        }
        if (location.state === "submitted" || location.state === "recount") {
            this.props.app.go("differences", { locationId: location.id, title: location.name });
        } else {
            this.props.app.go("count", { barId: location.id, title: location.name });
        }
    }

    async importPos(location) {
        try {
            const action = await this.model.fetch("desk_pos_import", {
                location_id: location.id,
                business_date: this.home.day.date,
            });
            await this.action.doAction(action);
        } catch (error) {
            this.state.error = errorMessage(error);
        }
    }

    async approve() {
        const day = this.home.day;
        const confirmed = await this.props.app.confirm(
            `Approve ${day.label}?`,
            "Every difference is posted to stock under its reason. This cannot be undone.",
            "Approve"
        );
        if (!confirmed) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_approve_day",
            { business_date: day.date },
            `Approve ${day.label}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        feedback(true);
        this.props.app.toast(
            outcome.status === "queued" ? "No connection: the approval is sent automatically." : `${day.label} approved.`,
            outcome.status === "queued" ? "warning" : "success"
        );
        await this.refresh();
    }

    open(screen, params = {}) {
        this.props.app.go(screen, params);
    }
}
