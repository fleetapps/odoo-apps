import { Component, useExternalListener, useState } from "@odoo/owl";
import { errorMessage, feedback } from "./desk_model";
import { initials } from "./utils";

const MAX_PIN = 12;

/** Sign-in: tap your name, type your PIN. Also the screen a locked Desk shows. */
export class LockScreen extends Component {
    static template = "odin_bar_desk.LockScreen";
    static props = { app: Object };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        this.state = useState({ employee: null, pin: "", error: "", busy: false });
        this.initials = initials;
        useExternalListener(window, "keydown", this.onKeydown.bind(this));
    }

    get dots() {
        return Array.from({ length: Math.max(4, this.state.pin.length) }, (_, i) => i < this.state.pin.length);
    }

    get pinKeys() {
        return ["1", "2", "3", "4", "5", "6", "7", "8", "9", "back", "0", "ok"];
    }

    pick(employee) {
        Object.assign(this.state, { employee, pin: "", error: "" });
    }

    notMe() {
        Object.assign(this.state, { employee: null, pin: "", error: "" });
    }

    press(key) {
        if (key === "ok") {
            return this.submit();
        }
        if (key === "back") {
            this.state.pin = this.state.pin.slice(0, -1);
        } else if (this.state.pin.length < MAX_PIN) {
            this.state.pin += key;
        }
        this.state.error = "";
    }

    onKeydown(ev) {
        if (!this.state.employee || this.state.busy) {
            return;
        }
        if (/^[0-9]$/.test(ev.key)) {
            this.press(ev.key);
        } else if (ev.key === "Backspace") {
            this.press("back");
        } else if (ev.key === "Enter") {
            this.submit();
        }
    }

    async submit(withoutPin = false) {
        if (this.state.busy || (!withoutPin && !this.state.pin)) {
            return;
        }
        this.state.busy = true;
        try {
            const error = await this.model.login(this.state.employee.id, withoutPin ? false : this.state.pin);
            if (error) {
                feedback(false);
                Object.assign(this.state, { error, pin: "" });
            } else {
                feedback(true);
            }
        } catch (error) {
            feedback(false);
            this.state.error = errorMessage(error);
        } finally {
            this.state.busy = false;
        }
    }

    async switchBar(ev) {
        await this.props.app.switchBar(parseInt(ev.target.value));
        this.notMe();
    }
}
