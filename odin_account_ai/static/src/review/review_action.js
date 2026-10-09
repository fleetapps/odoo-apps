import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { RecordSelector } from "@web/core/record_selectors/record_selector";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { useSetupAction } from "@web/search/action_hook";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { formatAmount } from "@odin_account_pnl/pnl/pnl_format";

const PL_TYPES = ["income", "income_other", "expense", "expense_other", "expense_depreciation", "expense_direct_cost"];

const GROUPS = [
    { key: "high", title: _t("Ready to accept"), help: _t("Your rules, or history that agrees") },
    { key: "medium", title: _t("Worth a look"), help: _t("A likely account, less history") },
    { key: "low", title: _t("Needs you"), help: _t("No history or no clear account") },
    { key: "reconcile", title: _t("Reconcile instead"), help: _t("Pays an open invoice or bill, or a transfer") },
];

/**
 * Transaction Review: bank lines still on suspense and draft bill lines on
 * the default account, each with a proposed account. The accountant goes
 * down the queue with the keyboard (J/K to move, A to accept, R to reject)
 * and changes the account where needed; nothing is booked until accepted.
 */
export class ReviewAction extends Component {
    static template = "odin_account_ai.ReviewAction";
    static components = { Layout, RecordSelector };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.rootRef = useRef("root");
        this.listRef = useRef("list");
        const restored = this.props.state?.odinReview;
        this.state = useState({
            data: null,
            selectedId: restored?.selectedId || null,
            edits: {},
            remember: false,
            generating: false,
            stop: false,
            working: false,
            filter: "all",
        });
        this.alive = true;
        this.pl_types = PL_TYPES;
        useSetupAction({
            getLocalState: () => ({ odinReview: { selectedId: this.state.selectedId } }),
        });
        onWillStart(() => this.load());
        onMounted(() => this.rootRef.el?.focus());
        onWillUnmount(() => {
            this.alive = false;
        });
    }

    async load() {
        this.state.data = await this.orm.call("odin.ai.suggestion", "inbox", []);
        if (!this.items.some((item) => item.id === this.state.selectedId)) {
            this.state.selectedId = this.ordered[0]?.id || null;
        }
    }

    // ------------------------------------------------------------------
    // The queue
    // ------------------------------------------------------------------

    get items() {
        return this.state.data?.items || [];
    }

    groupOf(item) {
        return item.state === "needs_reconcile" ? "reconcile" : item.band;
    }

    get groups() {
        return GROUPS.map((group) => ({
            ...group,
            items: this.items.filter((item) =>
                this.groupOf(item) === group.key &&
                (this.state.filter === "all" || item.source === this.state.filter)),
        })).filter((group) => group.items.length);
    }

    /** The queue in display order, for J/K. */
    get ordered() {
        return this.groups.flatMap((group) => group.items);
    }

    get selected() {
        return this.items.find((item) => item.id === this.state.selectedId) || null;
    }

    get highReady() {
        return this.items.filter((item) => item.state === "proposed" && item.band === "high" && item.account);
    }

    select(item) {
        this.state.selectedId = item?.id || null;
        this.state.remember = false;
        if (item) {
            const el = this.listRef.el?.querySelector(`[data-id="${item.id}"]`);
            el?.scrollIntoView({ block: "nearest" });
        }
    }

    step(delta) {
        const ordered = this.ordered;
        const index = ordered.findIndex((item) => item.id === this.state.selectedId);
        const next = ordered[Math.min(Math.max(index + delta, 0), ordered.length - 1)];
        if (next) {
            this.select(next);
        }
    }

    // ------------------------------------------------------------------
    // Editing the proposal
    // ------------------------------------------------------------------

    edit(item) {
        return this.state.edits[item.id] || {};
    }

    accountId(item) {
        return this.edit(item).account_id ?? (item.account ? item.account.id : false);
    }

    partnerId(item) {
        return this.edit(item).partner_id ?? (item.proposed_partner ? item.proposed_partner.id : false);
    }

    setAccount(item, id) {
        this.state.edits[item.id] = { ...this.edit(item), account_id: id || false };
    }

    setPartner(item, id) {
        this.state.edits[item.id] = { ...this.edit(item), partner_id: id || false };
    }

    get accountDomain() {
        return [["account_type", "in", PL_TYPES]];
    }

    // ------------------------------------------------------------------
    // Decisions
    // ------------------------------------------------------------------

    async accept(item = this.selected) {
        if (!item || item.state !== "proposed" || !this.state.data.can_apply || this.state.working) {
            return;
        }
        const accountId = this.accountId(item);
        if (!accountId) {
            this.notification.add(_t("Pick an account first."), { type: "warning" });
            return;
        }
        const edit = this.edit(item);
        await this.decide([item], "action_accept", [
            edit.account_id || false,
            edit.partner_id || false,
            this.state.remember,
        ]);
    }

    async acceptHigh() {
        const items = this.highReady;
        if (items.length) {
            await this.decide(items, "action_accept", [false, false, false]);
        }
    }

    async reject(item = this.selected) {
        if (!item || !this.state.data.can_apply || this.state.working) {
            return;
        }
        await this.decide([item], "action_reject", []);
    }

    async decide(items, method, args) {
        this.state.working = true;
        const ordered = this.ordered;
        const after = ordered[ordered.findIndex((i) => i.id === items.at(-1).id) + 1];
        try {
            const results = await this.orm.call("odin.ai.suggestion", method, [items.map((i) => i.id), ...args]);
            if (Array.isArray(results)) {
                const problems = results.filter((r) => r.error);
                const applied = results.length - problems.length;
                if (applied > 1) {
                    this.notification.add(_t("%s transactions categorised.", applied), { type: "success" });
                }
                for (const problem of problems) {
                    this.notification.add(problem.error, { type: problem.state === "stale" ? "warning" : "danger" });
                }
            }
            for (const item of items) {
                delete this.state.edits[item.id];
            }
            await this.load();
            if (after && this.items.some((i) => i.id === after.id)) {
                this.select(after);
            } else if (!this.selected) {
                this.select(this.ordered[0]);
            }
        } catch (error) {
            this.notify(error);
        } finally {
            this.state.working = false;
            this.state.remember = false;
            this.rootRef.el?.focus();
        }
    }

    // ------------------------------------------------------------------
    // Finding suggestions: one batch per call, repeated while the waiting
    // count goes down (the user can stop).
    // ------------------------------------------------------------------

    async generate() {
        this.state.generating = true;
        this.state.stop = false;
        try {
            let before = Infinity;
            while (this.alive && !this.state.stop) {
                this.state.data = await this.orm.call("odin.ai.suggestion", "action_generate", []);
                const pending = this.state.data.pending;
                if (!pending || pending >= before || this.state.data.ai) {
                    break;
                }
                before = pending;
            }
            if (!this.selected) {
                this.select(this.ordered[0]);
            }
        } catch (error) {
            this.notify(error);
        } finally {
            this.state.generating = false;
        }
    }

    async openDocument(item = this.selected, moveId = null) {
        if (!item) {
            return;
        }
        if (moveId) {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "account.move",
                res_id: moveId,
                views: [[false, "form"]],
            });
            return;
        }
        const action = await this.orm.call("odin.ai.suggestion", "action_open_document", [[item.id]]);
        await this.action.doAction(action);
    }

    // ------------------------------------------------------------------
    // Keyboard
    // ------------------------------------------------------------------

    onKeydown(ev) {
        if (ev.target.closest("input, textarea, select, .o-autocomplete") || ev.ctrlKey || ev.metaKey || ev.altKey) {
            return;
        }
        switch (ev.key) {
            case "j":
            case "ArrowDown":
                this.step(1);
                break;
            case "k":
            case "ArrowUp":
                this.step(-1);
                break;
            case "a":
                this.accept();
                break;
            case "r":
                this.reject();
                break;
            case "o":
                this.openDocument();
                break;
            default:
                return;
        }
        ev.preventDefault();
    }

    // ------------------------------------------------------------------
    // Display
    // ------------------------------------------------------------------

    fmt(value) {
        return formatAmount(value, { currencyDecimals: 2 });
    }

    originLabel(item) {
        return { rule: _t("Your rule"), match: _t("Open item"), ai: _t("AI") }[item.origin] || "";
    }

    notify(error) {
        this.notification.add(error.data?.message || error.message || String(error), { type: "danger" });
    }
}

registry.category("actions").add("odin_account_ai.review", ReviewAction);
