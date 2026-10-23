import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatFloat } from "@web/core/utils/numbers";
import { useDebounced } from "@web/core/utils/timing";
import { useSetupAction } from "@web/search/action_hook";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const MODEL = "odin.medical.quote";

/** 15822 → "15,822", 265.23 → "265.23": whole shillings without ".00". */
export function amount(value) {
    const number = Number(value) || 0;
    const whole = Math.abs(number - Math.round(number)) < 0.005;
    return formatFloat(whole ? Math.round(number) : number, { digits: [16, whole ? 0 : 2] });
}

/** "Children: 3 × 9,033", "Family of 5: 15,822", "5 × 1,210". */
export function partText(part) {
    const price = part.qty === 1 ? amount(part.price) : `${part.qty} × ${amount(part.price)}`;
    return part.who ? `${part.who}: ${price}` : price;
}

const BASIS = {
    family_size: _t("shared"),
    person: _t("per person"),
    policy: "",
};

function emptyCustomer() {
    return {
        source: "new",
        name: "",
        phone: "",
        email: "",
        source_id: false,
        partner_id: false,
        partner: null,
        lead_id: false,
        lead: null,
    };
}

function emptyCover() {
    return {
        principal_dob: "",
        include_spouse: false,
        spouse_dob: "",
        children: 0,
        children_only: false,
        limit_min: "",
        limit_max: "",
        insurer: "",
        template_id: "",
    };
}

/**
 * Add Medical Quote: the four steps of the example, on one page.
 *
 * 1. Client and cover details -- who the quote is for, who is covered, the
 *    inpatient limit range the client can afford.
 * 2. Cover options -- every plan in range, each option priced for this family,
 *    picked with one click.
 * 3. The chosen plans side by side, with a discount, the levies, a policy
 *    charge and the total, recomputed by the server on every change so the
 *    figures are the quotation's own.
 * 4. Saved: the client in Contacts, the opportunity in CRM, the quotations and
 *    the comparison -- print it, send it, or record that the client accepted.
 *
 * The page holds no prices of its own: every figure comes from the server.
 */
export class MedicalQuote extends Component {
    static template = "odin_medical_quote.MedicalQuote";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.amount = amount;
        this.partText = partText;
        this.basisLabel = (basis) => BASIS[basis] || "";
        this.state = useState({
            setup: null,
            customer: emptyCustomer(),
            cover: emptyCover(),
            // The cover the plans on screen were priced for. Totals and the
            // save use this, not what is being typed, so a half-edited form
            // never prices one family's options for another.
            searchedCover: null,
            coverDirty: false,
            coverOpen: true,
            results: null,
            showUnavailable: false,
            searching: false,
            selection: {},
            order: [],
            totals: {},
            totalsLoading: false,
            saving: false,
            saved: null,
            comparisonId: false,
            customerLocked: false,
            contactQuery: "",
            contactResults: [],
            leadQuery: "",
            leadResults: [],
            matches: [],
        });
        // Leaving for an invoice or a quotation and coming back through the
        // breadcrumb brings the screen back as it was left, not empty.
        useSetupAction({
            getLocalState: () => ({ quote: JSON.parse(JSON.stringify(this.exportable())) }),
        });
        this.refreshTotals = useDebounced(() => this.loadTotals(), 300);
        this.searchContacts = useDebounced(() => this.loadContacts(), 250);
        this.searchLeads = useDebounced(() => this.loadLeads(), 250);
        onWillStart(() => this.start());
    }

    get params() {
        return this.props.action?.params || {};
    }

    exportable() {
        const keep = [
            "customer", "cover", "searchedCover", "coverDirty", "coverOpen", "results",
            "selection", "order", "totals", "saved", "comparisonId", "customerLocked",
        ];
        return Object.fromEntries(keep.map((key) => [key, this.state[key]]));
    }

    async start() {
        const params = this.params;
        this.state.setup = await this.orm.call(MODEL, "get_setup", [], {
            lead_id: params.lead_id || false,
        });
        const restored = this.props.state?.quote;
        if (restored) {
            Object.assign(this.state, restored);
            if (this.state.saved && this.state.comparisonId) {
                // It may have been accepted in the meantime.
                this.state.saved = await this.orm.call(MODEL, "load_quote", [this.state.comparisonId]);
            }
            return;
        }
        const lead = this.state.setup.lead;
        if (lead) {
            this.pickLead(lead);
        }
        if (params.comparison_id) {
            await this.reopen(params.comparison_id);
        }
    }

    // -- step indicator ---------------------------------------------------

    get step() {
        if (this.state.saved) {
            return 4;
        }
        if (this.state.order.length) {
            return 3;
        }
        return this.state.results ? 2 : 1;
    }

    get steps() {
        return [
            { n: 1, label: _t("Client and cover") },
            { n: 2, label: _t("Cover options") },
            { n: 3, label: _t("Compare plans") },
            { n: 4, label: _t("Save and share") },
        ];
    }

    // -- the client -------------------------------------------------------

    setSource(source) {
        if (this.state.customerLocked) {
            return;
        }
        this.state.customer.source = source;
        this.state.matches = [];
    }

    onContactInput(ev) {
        this.state.contactQuery = ev.target.value;
        this.searchContacts();
    }

    async loadContacts() {
        const query = this.state.contactQuery;
        this.state.contactResults =
            query.trim().length < 2
                ? []
                : await this.orm.call(MODEL, "find_contacts", [query]);
    }

    pickContact(partner) {
        Object.assign(this.state.customer, {
            source: "existing",
            partner_id: partner.id,
            partner,
        });
        this.state.contactQuery = "";
        this.state.contactResults = [];
        this.state.matches = [];
    }

    clearContact() {
        Object.assign(this.state.customer, { partner_id: false, partner: null });
    }

    onLeadInput(ev) {
        this.state.leadQuery = ev.target.value;
        this.searchLeads();
    }

    async loadLeads() {
        this.state.leadResults = await this.orm.call(MODEL, "find_leads", [this.state.leadQuery]);
    }

    pickLead(lead) {
        Object.assign(this.state.customer, {
            source: "lead",
            lead_id: lead.id,
            lead,
            source_id: lead.source_id || false,
        });
        this.state.leadQuery = "";
        this.state.leadResults = [];
    }

    clearLead() {
        Object.assign(this.state.customer, { lead_id: false, lead: null });
    }

    async checkProspect() {
        const { source, name, phone, email } = this.state.customer;
        if (source !== "new" || (!phone && !email)) {
            this.state.matches = [];
            return;
        }
        this.state.matches = await this.orm.call(MODEL, "match_prospect", [name, phone, email]);
    }

    get customerSummary() {
        const customer = this.state.customer;
        if (customer.source === "existing" && customer.partner) {
            return customer.partner.name;
        }
        if (customer.source === "lead" && customer.lead) {
            return customer.lead.contact || customer.lead.name;
        }
        return customer.name || _t("New prospect");
    }

    customerProblem() {
        const customer = this.state.customer;
        if (this.state.customerLocked) {
            return "";
        }
        if (customer.source === "existing" && !customer.partner_id) {
            return _t("Choose the customer this quote is for.");
        }
        if (customer.source === "lead" && !customer.lead_id) {
            return _t("Choose the lead or opportunity this quote is for.");
        }
        if (customer.source === "new") {
            if (!customer.name.trim()) {
                return _t("Enter the prospect's name.");
            }
            if (!customer.phone.trim() && !customer.email.trim()) {
                return _t("Enter a phone number or an email for the prospect.");
            }
        }
        return "";
    }

    // -- who is covered and the range -------------------------------------

    onCoverChange() {
        if (this.state.results) {
            this.state.coverDirty = true;
        }
    }

    get planChoices() {
        const { insurer, children_only } = this.state.cover;
        return (this.state.setup?.plans || []).filter(
            (plan) =>
                (!insurer || plan.insurer === insurer) &&
                plan.type === (children_only ? "child_only" : "family")
        );
    }

    get coverSummary() {
        const cover = this.state.searchedCover || this.state.cover;
        const household = this.state.results?.household;
        const people = [];
        if (household) {
            if (household.principal !== null) {
                people.push(_t("principal %s", household.principal));
            }
            if (household.spouse !== null) {
                people.push(_t("spouse %s", household.spouse));
            }
            if (household.children) {
                people.push(
                    household.children === 1 ? _t("1 child") : _t("%s children", household.children)
                );
            }
        }
        let range;
        if (cover.template_id) {
            range = this.state.setup.plans.find((p) => p.id === Number(cover.template_id))?.name;
        } else if (cover.limit_min || cover.limit_max) {
            range = _t("limit %(min)s to %(max)s", {
                min: cover.limit_min ? amount(cover.limit_min) : "0",
                max: cover.limit_max ? amount(cover.limit_max) : _t("any"),
            });
        } else {
            range = _t("any limit");
        }
        return [people.join(", "), range, cover.insurer || _t("all insurers")]
            .filter(Boolean)
            .join(" · ");
    }

    async search() {
        const cover = this.state.cover;
        if (!cover.children_only && !cover.principal_dob) {
            this.notification.add(_t("Enter the principal's date of birth."), { type: "warning" });
            return;
        }
        if (cover.include_spouse && !cover.spouse_dob) {
            this.notification.add(_t("Enter the spouse's date of birth, or untick Include spouse."), {
                type: "warning",
            });
            return;
        }
        this.state.searching = true;
        try {
            const snapshot = JSON.parse(JSON.stringify(cover));
            const results = await this.orm.call(MODEL, "search_plans", [{ cover: snapshot }]);
            this.state.results = results;
            this.state.searchedCover = snapshot;
            this.state.coverDirty = false;
            this.state.coverOpen = false;
            this.pruneSelection();
            if (!results.plans.length) {
                this.state.coverOpen = true;
            }
            this.refreshTotals();
        } finally {
            this.state.searching = false;
        }
    }

    /** After a new search, keep what still exists, drop what does not. */
    pruneSelection() {
        const plans = new Map(this.state.results.plans.map((plan) => [plan.id, plan]));
        const dropped = [];
        for (const id of [...this.state.order]) {
            const plan = plans.get(id);
            if (!plan) {
                dropped.push(this.state.selection[id]?.title || "");
                this.removePlan(id, false);
                continue;
            }
            const keys = new Set(
                plan.groups.flatMap((g) => g.options.filter((o) => o.available).map((o) => o.key))
            );
            const selection = this.state.selection[id];
            selection.options = selection.options.filter((key) => keys.has(key));
        }
        if (dropped.length) {
            this.notification.add(
                _t("Taken off the quote, no longer offered for these details: %s", dropped.join(", ")),
                { type: "warning" }
            );
        }
    }

    // -- picking cover ----------------------------------------------------

    plan(id) {
        return this.state.results?.plans.find((plan) => plan.id === id);
    }

    get selectedPlans() {
        return this.state.order.map((id) => this.plan(id)).filter(Boolean);
    }

    get unavailablePlans() {
        return this.state.results?.unavailable || [];
    }

    isPlanSelected(plan) {
        return Boolean(this.state.selection[plan.id]);
    }

    isOptionSelected(plan, option) {
        return Boolean(this.state.selection[plan.id]?.options.includes(option.key));
    }

    addPlan(plan) {
        if (this.state.selection[plan.id]) {
            return;
        }
        this.state.selection[plan.id] = {
            title: plan.title,
            options: [],
            discount: "",
            policy_charge: "",
            recommended: false,
        };
        this.state.order.push(plan.id);
        this.refreshTotals();
    }

    removePlan(id, refresh = true) {
        delete this.state.selection[id];
        delete this.state.totals[id];
        this.state.order = this.state.order.filter((planId) => planId !== id);
        if (refresh) {
            this.refreshTotals();
        }
    }

    togglePlan(plan) {
        if (this.isPlanSelected(plan)) {
            this.removePlan(plan.id);
        } else {
            this.addPlan(plan);
        }
    }

    hasBenefit(plan, benefitKey) {
        const keys = this.state.selection[plan.id]?.options || [];
        return plan.groups.some(
            (group) =>
                group.benefit_key === benefitKey &&
                group.options.some((option) => keys.includes(option.key))
        );
    }

    benefitLabel(plan, benefitKey) {
        return plan.groups.find((group) => group.benefit_key === benefitKey)?.label || benefitKey;
    }

    /**
     * One option per benefit: picking another tier swaps it, picking the same
     * one again takes it off. Dental and optical that the insurer only sells
     * with outpatient cover cannot be added without it, and go when it goes.
     */
    toggleOption(plan, group, option) {
        if (!option.available) {
            return;
        }
        const had = Boolean(this.state.selection[plan.id]?.options.includes(option.key));
        // Checked before the plan is put on the quote: a refused click must
        // leave the quote as it was.
        if (!had && group.requires) {
            const offersIt = plan.groups.some((g) => g.benefit_key === group.requires);
            if (offersIt && !this.hasBenefit(plan, group.requires)) {
                this.notification.add(
                    _t("%(benefit)s is only sold with %(needed)s cover on %(plan)s. Add %(needed)s first.", {
                        benefit: group.label,
                        needed: this.benefitLabel(plan, group.requires),
                        plan: plan.name,
                    }),
                    { type: "warning" }
                );
                return;
            }
        }
        if (!this.isPlanSelected(plan)) {
            this.addPlan(plan);
        }
        const selection = this.state.selection[plan.id];
        const inGroup = new Set(group.options.map((o) => o.key));
        selection.options = selection.options.filter((key) => !inGroup.has(key));
        if (!had) {
            selection.options.push(option.key);
        } else {
            this.dropDependants(plan, group);
        }
        this.refreshTotals();
    }

    dropDependants(plan, group) {
        const selection = this.state.selection[plan.id];
        const dependants = plan.groups.filter(
            (g) =>
                g.requires === group.benefit_key &&
                g.options.some((o) => selection.options.includes(o.key))
        );
        if (!dependants.length || this.hasBenefit(plan, group.benefit_key)) {
            return;
        }
        const keys = new Set(dependants.flatMap((g) => g.options.map((o) => o.key)));
        selection.options = selection.options.filter((key) => !keys.has(key));
        this.notification.add(
            _t("%(removed)s taken off too: it is only sold with %(benefit)s.", {
                removed: dependants.map((g) => g.label).join(", "),
                benefit: group.label,
            }),
            { type: "info" }
        );
    }

    /** The options picked on a plan, with their benefit, for its card. */
    chosen(plan) {
        const keys = this.state.selection[plan.id]?.options || [];
        const rows = [];
        for (const group of plan.groups) {
            const option = group.options.find((o) => keys.includes(o.key));
            if (option) {
                rows.push({ group, option });
            }
        }
        return rows;
    }

    optionLabel(option) {
        return option.label || (option.limit ? _t("Limit %s", amount(option.limit)) : option.name);
    }

    toggleRecommended(plan) {
        const selection = this.state.selection[plan.id];
        const value = !selection.recommended;
        for (const id of this.state.order) {
            this.state.selection[id].recommended = false;
        }
        selection.recommended = value;
    }

    // -- totals -----------------------------------------------------------

    payload() {
        return {
            customer: { ...this.state.customer, partner: undefined, lead: undefined },
            cover: this.state.searchedCover || this.state.cover,
            plans: this.state.order.map((id) => {
                const { options, discount, policy_charge, recommended } = this.state.selection[id];
                return { template_id: id, options: [...options], discount, policy_charge, recommended };
            }),
            comparison_id: this.state.comparisonId || false,
        };
    }

    async loadTotals() {
        if (!this.state.order.length || !this.state.searchedCover) {
            return;
        }
        this.state.totalsLoading = true;
        try {
            const totals = await this.orm.call(MODEL, "compute_totals", [this.payload()]);
            for (const [id, value] of Object.entries(totals)) {
                this.state.totals[id] = value;
            }
        } finally {
            this.state.totalsLoading = false;
        }
    }

    totalsFor(plan) {
        return this.state.totals[plan.id] || null;
    }

    // -- saving -----------------------------------------------------------

    get saveProblem() {
        if (!this.state.order.length) {
            return _t("Select at least one plan.");
        }
        if (this.state.coverDirty) {
            return _t("The cover details changed: press Update plans to price them.");
        }
        const failing = this.selectedPlans.find((plan) => this.totalsFor(plan)?.error);
        if (failing) {
            return `${failing.name}: ${this.totalsFor(failing).error}`;
        }
        return this.customerProblem();
    }

    async save() {
        const problem = this.saveProblem;
        if (problem) {
            this.notification.add(problem, { type: "warning" });
            return;
        }
        this.state.saving = true;
        try {
            const saved = await this.orm.call(MODEL, "save_quote", [this.payload()]);
            this.state.saved = saved;
            this.state.comparisonId = saved.comparison.id;
            this.state.customerLocked = true;
        } finally {
            this.state.saving = false;
        }
    }

    async reopen(comparisonId) {
        const data = await this.orm.call(MODEL, "load_quote", [comparisonId]);
        this.state.comparisonId = data.comparison.id;
        this.state.customerLocked = true;
        if (data.comparison.state === "accepted" || !data.params) {
            this.state.saved = data;
            return;
        }
        const params = data.params;
        Object.assign(this.state.customer, params.customer || {}, {
            partner: data.partner,
            lead: data.lead || null,
        });
        Object.assign(this.state.cover, params.cover || {});
        await this.search();
        for (const plan of params.plans || []) {
            const offered = this.plan(plan.template_id);
            if (!offered) {
                continue;
            }
            this.addPlan(offered);
            Object.assign(this.state.selection[offered.id], {
                options: plan.options || [],
                discount: plan.discount || "",
                policy_charge: plan.policy_charge || "",
                recommended: Boolean(plan.recommended),
            });
        }
        this.pruneSelection();
        this.loadTotals();
    }

    editAgain() {
        this.state.saved = null;
    }

    newQuote() {
        this.action.doAction("odin_medical_quote.action_medical_quote", { clearBreadcrumbs: false });
    }

    // -- after saving -----------------------------------------------------

    async callComparison(method) {
        const action = await this.orm.call("odin.quote.comparison", method, [[this.state.comparisonId]]);
        if (action && typeof action === "object") {
            await this.action.doAction(action);
        }
    }

    openComparison() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "odin.quote.comparison",
            res_id: this.state.comparisonId,
            views: [[false, "form"]],
        });
    }

    openRecord(model, id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
        });
    }

    accept(order) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Client accepted"),
            res_model: "odin.medical.quote.accept",
            views: [[false, "form"]],
            target: "new",
            context: {
                default_comparison_id: this.state.comparisonId,
                default_order_id: order.id,
                odin_plan_label: true,
            },
        });
    }
}

registry.category("actions").add("odin_medical_quote", MedicalQuote);
