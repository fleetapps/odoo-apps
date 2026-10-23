import { Component, onMounted, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";

const STEPS = [
    { key: "vehicle", label: _t("Your vehicle") },
    { key: "options", label: _t("Prices") },
    { key: "details", label: _t("Your quote") },
];

const PERIODS = { 12: _t("A year"), 6: _t("6 months"), 1: _t("1 month") };

/**
 * The online motor quote page: what you drive and the cover you want, every
 * insurer's price with the extras each offers, where to send the quote. It
 * shares its look, its steps and its last step with the medical page; every
 * price comes from the server.
 */
export class OnlineMotorQuote extends Component {
    static template = "odin_motor_quote_online.OnlineMotorQuote";
    static props = {
        company: Object,
        classes: Array,
        years: Array,
        extras: Array,
        maxPlans: Number,
        prefill: Object,
        tokens: Object,
    };

    setup() {
        const prefill = this.props.prefill;
        const vehicle = prefill.vehicle || {};
        this.state = useState({
            step: "vehicle",
            vehicle: {
                class_id: Number(vehicle.class_id) || 0,
                cover: vehicle.cover || "",
                value: vehicle.value || "",
                year: Number(vehicle.year) || 0,
                seats: vehicle.seats || "",
                tonnage: vehicle.tonnage || "",
                period: Number(vehicle.period) || 12,
            },
            extras: prefill.extras || {},
            plans: [],
            refused: "",
            loaded: false,
            loading: false,
            refreshing: {},
            chosen: [],
            contact: {
                name: prefill.name || "",
                phone: prefill.phone || "",
                email: prefill.email || "",
                contact_by: prefill.contact_by || "whatsapp",
                registration: prefill.registration || "",
                make_model: prefill.make_model || "",
                consent: false,
                website: "",
            },
            error: "",
            submitting: false,
        });
        this.calls = 0;
        onMounted(() => {
            if (this.state.vehicle.class_id && this.state.vehicle.cover) {
                // Back from their quote to change it: straight to the prices.
                this.showPlans();
            }
        });
    }

    // -- shared with the medical page's templates -----------------------------

    get steps() {
        return STEPS;
    }

    get stepIndex() {
        return this.steps.findIndex((step) => step.key === this.state.step);
    }

    get backLabel() {
        return _t("Back to the prices");
    }

    money(value, decimals = 2) {
        const number = Number(value || 0).toLocaleString("en-US", {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        });
        const { currency, currencyAfter } = this.props.company;
        return currencyAfter ? `${number} ${currency}` : `${currency} ${number}`;
    }

    get whatsappUrl() {
        const number = this.props.company.whatsapp;
        if (!number) {
            return "";
        }
        const text = _t("Hi %s, I'd like help with motor insurance.", this.props.company.name);
        return `https://wa.me/${number}?text=${encodeURIComponent(text)}`;
    }

    back(step) {
        this.state.error = "";
        this.state.step = step;
        window.scrollTo(0, 0);
    }

    // -- step 1: the vehicle ----------------------------------------------------

    get vehicle() {
        return this.state.vehicle;
    }

    get vehicleClass() {
        return this.props.classes.find((c) => c.id === this.vehicle.class_id);
    }

    pickClass(vehicleClass) {
        this.vehicle.class_id = vehicleClass.id;
        const covers = vehicleClass.covers.map((c) => c.key);
        if (!covers.includes(this.vehicle.cover)) {
            this.vehicle.cover = covers.length === 1 ? covers[0] : "";
        }
        if (!vehicleClass.periods.includes(this.vehicle.period)) {
            this.vehicle.period = vehicleClass.periods[0] || 12;
        }
        this.state.error = "";
    }

    pickCover(key) {
        this.vehicle.cover = key;
        this.state.error = "";
    }

    get isComprehensive() {
        return this.vehicle.cover === "comprehensive";
    }

    get askSeats() {
        return Boolean(this.vehicleClass && this.vehicleClass.ask_seats);
    }

    get askTonnage() {
        return Boolean(this.vehicleClass && this.vehicleClass.ask_tonnage && this.vehicle.cover === "tpo");
    }

    get periods() {
        const periods = (this.vehicleClass && this.vehicleClass.periods) || [];
        return this.vehicle.cover === "tpo" && periods.length > 1
            ? periods.map((months) => ({ key: months, label: PERIODS[months] || _t("%s months", months) }))
            : [];
    }

    get valueText() {
        const value = Number(this.vehicle.value);
        return value >= 1000 ? this.money(value, 0) : "";
    }

    vehicleProblem() {
        const v = this.vehicle;
        if (!this.vehicleClass) {
            return _t("Choose what you drive.");
        }
        if (!v.cover) {
            return _t("Choose full cover or third party only.");
        }
        if (this.isComprehensive && !(Number(v.value) >= 10000)) {
            return _t("Enter what the vehicle is worth today, in %s.", this.props.company.currency);
        }
        if (!v.year) {
            return _t("Choose the year it was made.");
        }
        if (this.askSeats && !(Number(v.seats) >= 1)) {
            return _t("Enter how many passengers it can carry.");
        }
        if (this.askTonnage && !(Number(v.tonnage) > 0)) {
            return _t("Enter how many tonnes it can carry.");
        }
        return "";
    }

    get vehicleSummary() {
        const v = this.vehicle;
        const parts = [this.vehicleClass ? this.vehicleClass.name : ""];
        const cover = this.vehicleClass && this.vehicleClass.covers.find((c) => c.key === v.cover);
        if (cover) {
            parts.push(cover.label);
        }
        if (this.isComprehensive) {
            parts.push(this.money(v.value, 0));
        }
        if (v.year) {
            parts.push(String(v.year));
        }
        if (this.askSeats) {
            parts.push(_t("%s passengers", v.seats));
        }
        if (this.askTonnage) {
            parts.push(_t("%s tonnes", v.tonnage));
        }
        if (this.periods.length) {
            parts.push(PERIODS[v.period] || "");
        }
        return parts.filter(Boolean).join(" · ");
    }

    showPlans() {
        const problem = this.vehicleProblem();
        if (problem) {
            this.state.error = problem;
            return;
        }
        this.state.error = "";
        this.state.step = "options";
        this.state.chosen = [];
        window.scrollTo(0, 0);
        this.loadPlans();
    }

    // -- step 2: the prices -----------------------------------------------------

    vehicleParams() {
        const v = this.vehicle;
        return {
            class_id: v.class_id,
            cover: v.cover,
            value: this.isComprehensive ? Number(v.value) : 0,
            year: Number(v.year),
            seats: this.askSeats ? Number(v.seats) : 0,
            tonnage: this.askTonnage ? Number(v.tonnage) : 0,
            period: this.periods.length ? Number(v.period) : 12,
            registration: this.state.contact.registration,
            make_model: this.state.contact.make_model,
        };
    }

    async loadPlans() {
        const call = ++this.calls;
        this.state.loading = true;
        const result = await rpc("/motor-quote/options", {
            vehicle: this.vehicleParams(),
            extras: this.state.extras,
        });
        if (call !== this.calls) {
            return;
        }
        this.state.loading = false;
        this.state.loaded = true;
        if (result.error) {
            this.state.error = result.error;
            this.state.plans = [];
            return;
        }
        this.state.error = "";
        this.state.plans = result.plans;
        this.state.refused = result.refused;
        const ids = new Set(result.plans.map((p) => p.id));
        this.state.chosen = this.state.chosen.filter((id) => ids.has(id));
    }

    async setExtra(plan, extra, optionKey) {
        const picked = new Set(this.state.extras[plan.id] || []);
        extra.options.forEach((option) => picked.delete(option.key));
        if (optionKey) {
            picked.add(optionKey);
        }
        this.state.extras[plan.id] = [...picked];
        this.state.refreshing[plan.id] = true;
        const result = await rpc("/motor-quote/options", {
            vehicle: this.vehicleParams(),
            extras: this.state.extras,
            only: [plan.id],
        });
        this.state.refreshing[plan.id] = false;
        if (result.error || !result.plans.length) {
            this.state.error = result.error || "";
            return;
        }
        const index = this.state.plans.findIndex((p) => p.id === plan.id);
        if (index >= 0) {
            this.state.plans[index] = result.plans[0];
        }
    }

    toggleExtra(plan, extra) {
        this.setExtra(plan, extra, extra.chosen ? "" : extra.options[0].key);
    }

    isChosen(plan) {
        return this.state.chosen.includes(plan.id);
    }

    toggleChosen(plan) {
        if (this.isChosen(plan)) {
            this.state.chosen = this.state.chosen.filter((id) => id !== plan.id);
            return;
        }
        if (this.state.chosen.length >= this.props.maxPlans) {
            this.state.error = _t("You can put up to %s insurers on your quote.", this.props.maxPlans);
            return;
        }
        this.state.error = "";
        this.state.chosen = [...this.state.chosen, plan.id];
    }

    get chosenPlans() {
        return this.state.plans.filter((p) => this.state.chosen.includes(p.id));
    }

    toDetails() {
        if (!this.state.chosen.length) {
            return;
        }
        this.state.error = "";
        this.state.step = "details";
        window.scrollTo(0, 0);
    }

    // -- step 3: where to send it -----------------------------------------------

    async submit() {
        const c = this.state.contact;
        if (!c.name.trim()) {
            this.state.error = _t("Enter your name.");
            return;
        }
        if (c.phone.replace(/\D/g, "").length < 9) {
            this.state.error = _t("Enter a phone number we can reach you on, like 0712 345 678.");
            return;
        }
        if (!c.consent) {
            this.state.error = _t("Tick the box to let us contact you about your quote.");
            return;
        }
        this.state.error = "";
        this.state.submitting = true;
        const result = await rpc("/motor-quote/submit", {
            vehicle: this.vehicleParams(),
            extras: this.state.extras,
            chosen: this.state.chosen,
            contact: { ...c },
            tokens: this.props.tokens,
        });
        if (result.error) {
            this.state.submitting = false;
            this.state.error = result.error;
            return;
        }
        window.location.assign(result.url);
    }
}

registry.category("public_components").add("odin_motor_quote_online.OnlineMotorQuote", OnlineMotorQuote);
