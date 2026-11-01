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
    { key: "any", label: _t("Any amount"), test: () => true },
    { key: "low", label: _t("Up to 300K"), test: (limit) => limit <= 300000 },
    { key: "mid", label: _t("300K to 1M"), test: (limit) => limit > 300000 && limit <= 1000000 },
    { key: "high", label: _t("Over 1M"), test: (limit) => limit > 1000000 },
];

/**
 * Where the cards start: each insurer's card opens on its plan nearest this
 * hospital cover, so the cards compare like with like.
 */
const TARGET_LIMIT = 500000;

const STEPS = [
    { key: "who", label: _t("You") },
    { key: "options", label: _t("Plans") },
    { key: "details", label: _t("Your quote") },
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
            // The plan picked on each insurer's card, by card.
            picks: {},
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
            if (prefill.who && !this.familyProblem()) {
                // Back from their quote to change it: straight to the plans.
                // A link that only says who needs cover still asks the ages.
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

    get steps() {
        return STEPS;
    }

    get stepIndex() {
        return this.steps.findIndex((step) => step.key === this.state.step);
    }

    get backLabel() {
        return _t("Back to the plans");
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
        this.state.picks = {};
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

    pickBand(key) {
        // Each card goes back to its plan for that much cover.
        this.state.band = key;
        this.state.picks = {};
    }

    /**
     * One card per insurer's product: the plans in it differ only by how
     * much hospital cover they give. A card shows the plan picked on it, the
     * one on the quote, or else its plan for the cover asked for: nearest to
     * 500,000, or the cheapest in the band tapped. Cards are ordered by that
     * starting price, so they do not jump when a visitor changes one.
     */
    get cards() {
        const band = BANDS.find((b) => b.key === this.state.band) || BANDS[0];
        const groups = new Map();
        for (const plan of this.state.plans) {
            if (!groups.has(plan.group)) {
                groups.set(plan.group, []);
            }
            groups.get(plan.group).push(plan);
        }
        const cards = [];
        for (const [key, plans] of groups) {
            plans.sort((a, b) => a.limit - b.limit || a.name.length - b.name.length);
            const start = band.key === "any" ? nearest(plans) : cheapest(plans.filter((p) => band.test(p.limit)));
            const plan =
                plans.find((p) => p.id === this.state.picks[key]) ||
                plans.find((p) => this.isChosen(p)) ||
                start;
            if (plan) {
                cards.push({ key, plans, plan, rank: (start || plan).total });
            }
        }
        return cards.sort((a, b) => a.rank - b.rank || a.key.localeCompare(b.key));
    }

    get lowestCard() {
        return cheapest(this.cards.map((card) => card.plan));
    }

    /** The hospital cover amounts a card offers, each with its plain plan. */
    amounts(card) {
        const seen = new Map();
        for (const plan of card.plans) {
            if (!seen.has(plan.limit)) {
                seen.set(plan.limit, plan);
            }
        }
        return [...seen.values()];
    }

    /**
     * Plans of the card at the amount shown, the plain one first: GA sells
     * each amount with or without pandemic cover.
     */
    variants(card) {
        return card.plans.filter((p) => p.limit === card.plan.limit);
    }

    variantLabel(plain, plan) {
        // "KES 1,000,000, with pandemic cover" after "KES 1,000,000".
        const extra = plan.name.startsWith(plain.name)
            ? plan.name.slice(plain.name.length).replace(/^[\s,–-]+/, "")
            : plan.name;
        return _t("Add %s", extra.replace(/^with\s+/i, ""));
    }

    pickAmount(card, limit) {
        const current = this.variants(card);
        const suffix = card.plan.name.slice(current[0].name.length);
        const atLimit = card.plans.filter((p) => p.limit === Number(limit));
        const same = atLimit.find((p) => suffix && p.name.endsWith(suffix) && p !== atLimit[0]);
        this.pickPlan(card, same || atLimit[0]);
    }

    toggleVariant(card) {
        const [plain, other] = this.variants(card);
        this.pickPlan(card, card.plan.id === plain.id ? other : plain);
    }

    /**
     * Put ``target`` on the card in place of its plan: on the quote too if
     * the card's plan was, and with the same doctor-visit, dental or optical
     * choices where the target offers them.
     */
    async pickPlan(card, target) {
        const from = card.plan;
        if (!target || target.id === from.id) {
            return;
        }
        if (this.isChosen(from)) {
            this.state.chosen = this.state.chosen.map((id) => (id === from.id ? target.id : id));
        }
        this.state.picks[card.key] = target.id;
        const tiers = {};
        for (const [key, optionKey] of Object.entries(this.state.tiers[from.id] || {})) {
            const had = from.benefits.find((b) => b.key === key)?.options.find((o) => o.key === optionKey);
            const offer = target.benefits.find((b) => b.key === key);
            const same = had && offer?.options.find((o) =>
                had.label ? o.label === had.label : o.limit === had.limit);
            if (same) {
                tiers[key] = same.key;
            }
        }
        if (Object.keys(tiers).length) {
            this.state.tiers[target.id] = tiers;
            await this.refreshPlan(target);
        }
    }

    async setTier(plan, benefit, optionKey) {
        const tiers = { ...(this.state.tiers[plan.id] || {}), [benefit.key]: optionKey };
        this.state.tiers[plan.id] = tiers;
        await this.refreshPlan(plan);
    }

    /** Price one plan again, with the choices made on it. */
    async refreshPlan(plan) {
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

/** The plan whose cover is nearest 500,000, the smaller and cheaper on a tie. */
function nearest(plans) {
    return [...plans].sort((a, b) =>
        Math.abs(a.limit - TARGET_LIMIT) - Math.abs(b.limit - TARGET_LIMIT) ||
        a.limit - b.limit ||
        a.total - b.total
    )[0];
}

function cheapest(plans) {
    return [...plans].sort((a, b) => a.total - b.total)[0];
}

registry.category("public_components").add("odin_medical_quote_online.OnlineQuote", OnlineQuote);
