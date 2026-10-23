import { Component, onMounted, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";

const WHO = [
    { key: "me", icon: "fa-user", label: _t("Just me") },
    { key: "couple", icon: "fa-heart", label: _t("Me and my partner") },
    { key: "me_kids", icon: "fa-child", label: _t("Me and my children") },
    { key: "family", icon: "fa-users", label: _t("My whole family"), hint: _t("Me, my partner and our children") },
    { key: "kids", icon: "fa-child", label: _t("Only my children") },
];

/** Hospital cover bands, so nobody has to know what a limit is. */
const BANDS = [
    { key: "any", label: _t("Any"), test: () => true },
    { key: "low", label: _t("Up to 300,000"), test: (limit) => limit <= 300000 },
    { key: "mid", label: _t("300,000 to 1M"), test: (limit) => limit > 300000 && limit <= 1000000 },
    { key: "high", label: _t("Over 1M"), test: (limit) => limit > 1000000 },
];

/**
 * The online quote page: who needs cover, the plans that fit with their
 * price, where to send the quote. Every price comes from the server, worked
 * out the way the office's own quotes are.
 */
export class OnlineQuote extends Component {
    static template = "odin_medical_quote_online.OnlineQuote";
    static props = {
        company: Object,
        benefits: Array,
        maxPlans: Number,
        prefill: Object,
        tokens: Object,
    };

    setup() {
        const prefill = this.props.prefill;
        this.who = WHO;
        this.state = useState({
            step: "who",
            family: {
                who: prefill.who || "",
                age: prefill.age || "",
                partner_age: prefill.partner_age || "",
                children: prefill.children || "",
            },
            wants: prefill.wants || [],
            band: "any",
            tiers: {},
            plans: [],
            offered: this.props.benefits.map((b) => b.key),
            loaded: false,
            loading: false,
            refreshing: {},
            chosen: [],
            contact: {
                name: prefill.name || "",
                phone: prefill.phone || "",
                email: prefill.email || "",
                contact_by: prefill.contact_by || "whatsapp",
                consent: false,
                website: "",
            },
            error: "",
            submitting: false,
        });
        this.calls = 0;
        onMounted(() => {
            if (prefill.who) {
                // Back from their quote to change it: straight to the plans.
                this.showPlans();
            }
        });
    }

    // -- formatting ----------------------------------------------------------

    money(value, decimals = 2) {
        const number = Number(value || 0).toLocaleString("en-US", {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        });
        const { currency, currencyAfter } = this.props.company;
        return currencyAfter ? `${number} ${currency}` : `${currency} ${number}`;
    }

    whole(value) {
        return Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
    }

    get whatsappUrl() {
        const number = this.props.company.whatsapp;
        if (!number) {
            return "";
        }
        const text = _t("Hi %s, I'd like help choosing medical cover.", this.props.company.name);
        return `https://wa.me/${number}?text=${encodeURIComponent(text)}`;
    }

    // -- step 1: who ---------------------------------------------------------

    get family() {
        return this.state.family;
    }

    get needsAge() {
        return this.family.who && this.family.who !== "kids";
    }

    get needsPartner() {
        return ["couple", "family"].includes(this.family.who);
    }

    get needsChildren() {
        return ["me_kids", "family", "kids"].includes(this.family.who);
    }

    pickWho(key) {
        this.family.who = key;
        this.state.error = "";
    }

    familyProblem() {
        const f = this.family;
        const adult = (value) => Number(value) >= 18 && Number(value) <= 99;
        if (!f.who) {
            return _t("Choose who needs cover.");
        }
        if (this.needsAge && !adult(f.age)) {
            return _t("Enter your age (18 to 99).");
        }
        if (this.needsPartner && !adult(f.partner_age)) {
            return _t("Enter your partner's age (18 to 99).");
        }
        if (this.needsChildren && !(Number(f.children) >= 1 && Number(f.children) <= 8)) {
            return _t("Enter how many children need cover (1 to 8).");
        }
        return "";
    }

    get familySummary() {
        const f = this.family;
        const parts = [];
        if (this.needsAge) {
            parts.push(_t("you (%s)", f.age));
        }
        if (this.needsPartner) {
            parts.push(_t("your partner (%s)", f.partner_age));
        }
        if (this.needsChildren) {
            parts.push(Number(f.children) === 1 ? _t("1 child") : _t("%s children", f.children));
        }
        return parts.join(", ");
    }

    showPlans() {
        const problem = this.familyProblem();
        if (problem) {
            this.state.error = problem;
            return;
        }
        this.state.error = "";
        this.state.step = "options";
        this.state.chosen = [];
        this.state.tiers = {};
        window.scrollTo(0, 0);
        this.loadPlans();
    }

    // -- step 2: plans -------------------------------------------------------

    familyParams() {
        const f = this.family;
        return {
            who: f.who,
            age: this.needsAge ? Number(f.age) : null,
            partner_age: this.needsPartner ? Number(f.partner_age) : null,
            children: this.needsChildren ? Number(f.children) : 0,
        };
    }

    async loadPlans() {
        const call = ++this.calls;
        this.state.loading = true;
        const result = await rpc("/medical-quote/options", {
            family: this.familyParams(),
            wants: this.state.wants,
            tiers: this.state.tiers,
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
        this.state.offered = result.offered;
        const ids = new Set(result.plans.map((p) => p.id));
        this.state.chosen = this.state.chosen.filter((id) => ids.has(id));
    }

    get offeredBenefits() {
        return this.props.benefits.filter((b) => this.state.offered.includes(b.key));
    }

    isWanted(key) {
        return this.state.wants.includes(key);
    }

    toggleWant(key) {
        const wants = new Set(this.state.wants);
        wants.has(key) ? wants.delete(key) : wants.add(key);
        this.state.wants = this.props.benefits.map((b) => b.key).filter((k) => wants.has(k));
        this.loadPlans();
    }

    get bands() {
        return BANDS.filter(
            (band) => band.key === "any" || this.state.plans.some((p) => band.test(p.limit))
        );
    }

    get shownPlans() {
        const band = BANDS.find((b) => b.key === this.state.band) || BANDS[0];
        return this.state.plans.filter((p) => band.test(p.limit));
    }

    async setTier(plan, benefit, optionKey) {
        const tiers = { ...(this.state.tiers[plan.id] || {}), [benefit.key]: optionKey };
        this.state.tiers[plan.id] = tiers;
        this.state.refreshing[plan.id] = true;
        const result = await rpc("/medical-quote/options", {
            family: this.familyParams(),
            wants: this.state.wants,
            tiers: this.state.tiers,
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

    isChosen(plan) {
        return this.state.chosen.includes(plan.id);
    }

    toggleChosen(plan) {
        if (this.isChosen(plan)) {
            this.state.chosen = this.state.chosen.filter((id) => id !== plan.id);
            return;
        }
        if (this.state.chosen.length >= this.props.maxPlans) {
            this.state.error = _t("You can put up to %s plans on your quote.", this.props.maxPlans);
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

    // -- step 3: details -----------------------------------------------------

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
        const result = await rpc("/medical-quote/submit", {
            family: this.familyParams(),
            wants: this.state.wants,
            tiers: this.state.tiers,
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

    back(step) {
        this.state.error = "";
        this.state.step = step;
        window.scrollTo(0, 0);
    }
}

registry.category("public_components").add("odin_medical_quote_online.OnlineQuote", OnlineQuote);
