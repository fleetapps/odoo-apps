import { Component, onMounted, onWillStart, useEffect, useExternalListener, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { useSetupAction } from "@web/search/action_hook";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { PnlFilterBar } from "./pnl_filters";
import { PnlShortcutsDialog } from "./pnl_shortcuts_dialog";
import { PnlSidePanel } from "./pnl_side_panel";
import { PnlViewsMenu } from "./pnl_views_menu";
import { download } from "@web/core/network/download";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { formatAmount, formatGrowth, formatPercent, sparkPoints } from "./pnl_format";
import { lineActionRegistry, sidePanelRegistry } from "./pnl_registries";

// Display choices remembered per browser. The period and filters are not:
// the report opens on last month, as Enterprise's does. ".v2": the first key
// stored the 12-month trend's old default (on) for everyone, which cannot be
// told apart from a choice; the trend is now off unless turned on.
const PREFS_KEY = "odin_account_pnl.display.v2";
const REMEMBERED = ["layout_id", "scale", "decimals", "show_codes", "trend", "percent_of_base",
    "hide_zero", "account_groups", "negative_parentheses"];
// The one-time tip about clicks and shortcuts, once dismissed in this browser.
const TIP_KEY = "odin_account_pnl.tip_dismissed";

function readPrefs() {
    try {
        return JSON.parse(browser.localStorage.getItem(PREFS_KEY)) || {};
    } catch {
        return {};
    }
}

function readTipDismissed() {
    try {
        return browser.localStorage.getItem(TIP_KEY) === "1";
    } catch {
        return false;
    }
}

function writePrefs(options) {
    try {
        const prefs = Object.fromEntries(REMEMBERED.map((key) => [key, options[key]]));
        browser.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch {
        // Private window or storage blocked: the choice just is not remembered.
    }
}

/** What a row can be split by ("By ▾"), depending on what it is. */
function modesFor(row, plans) {
    const modes = [];
    const kind = row.key.split("/").at(-1).split(":")[0];
    if (kind === "L" || kind === "G") {
        modes.push(["accounts", _t("Accounts")]);
    }
    modes.push(["entries", _t("Journal items")], ["ledger", _t("Ledger")]);
    modes.push(
        ["partner", _t("Partner")],
        ["product", _t("Product")],
        ["product_category", _t("Product category")],
        ["month", _t("Month")],
        ["journal", _t("Journal")],
    );
    for (const plan of plans) {
        modes.push([`analytic:${plan.id}`, plan.name]);
    }
    return modes;
}

/**
 * The interactive Profit & Loss: one page, as Odoo 19 Enterprise's. Lines
 * unfold in place into accounts, contributors and journal items; documents
 * open in the side panel; a figure lists the items behind it. All figures
 * come from odin.pnl.report (orm.call), which also re-applies the unfolded
 * rows on every reload, so a filter change keeps what is open.
 */
export class PnlAction extends Component {
    static template = "odin_account_pnl.PnlAction";
    static components = { Layout, PnlFilterBar, PnlSidePanel, PnlViewsMenu, Dropdown, DropdownItem };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.formatGrowth = formatGrowth;
        this.formatPercent = formatPercent;
        this.sparkPoints = sparkPoints;
        const restored = this.props.state?.odinPnl;
        this.state = useState({
            loading: true,
            busy: false,
            error: "",
            data: null,
            rows: [],
            options: restored?.options || { ...readPrefs(), ...(this.props.action.params?.options || {}) },
            panel: restored?.panel || null,
            // params.focus: a row to show, e.g. from an evidence link of the AI module.
            focusKey: restored?.focusKey || this.props.action.params?.focus || null,
            menu: null,
            search: "",
            sort: restored?.sort || null,
            editing: null,
            viewId: restored?.viewId || false,
            exporting: false,
            tipDismissed: readTipDismissed(),
        });
        this.journals = [];
        this.rootRef = useRef("root");
        this.scrollerRef = useRef("scroller");
        this.restoredScroll = restored?.scrollTop || 0;
        useSetupAction({
            getLocalState: () => ({
                odinPnl: {
                    options: this.state.options,
                    panel: this.state.panel,
                    focusKey: this.state.focusKey,
                    sort: this.state.sort,
                    viewId: this.state.viewId,
                    scrollTop: this.scrollerRef.el?.scrollTop || 0,
                },
            }),
        });
        useExternalListener(window, "click", this.onWindowClick.bind(this), { capture: true });
        onWillStart(async () => {
            if (!restored && !this.props.action.params?.options) {
                // The user's default saved view, its period computed from today.
                const options = await this.orm.call("odin.pnl.report", "get_view_options", [false]);
                if (options) {
                    this.state.options = options;
                }
            }
            const [journals] = await Promise.all([
                this.orm.searchRead("account.journal", [], ["name", "code"], { order: "sequence, name" }),
                this.load(),
            ]);
            this.journals = journals;
        });
        onMounted(() => {
            if (this.scrollerRef.el && this.restoredScroll) {
                this.scrollerRef.el.scrollTop = this.restoredScroll;
            } else if (!restored && this.state.focusKey) {
                this.scrollIntoView(this.state.focusKey, "center");
            }
        });
        useEffect(
            (editing) => {
                if (editing) {
                    const input = this.scrollerRef.el?.querySelector(".o_odin_pnl_budget_input");
                    input?.focus();
                    input?.select();
                }
            },
            () => [this.state.editing]
        );
        // A menu opened from the keyboard (B) takes the focus, so its items
        // are reached with the arrows and chosen with Enter.
        useEffect(
            (menu) => {
                if (menu?.fromKeyboard) {
                    this.rootRef.el?.querySelector(".o_odin_pnl_rowmenu .dropdown-item")?.focus();
                }
            },
            () => [this.state.menu]
        );
    }

    // ------------------------------------------------------------------
    // Loading
    // ------------------------------------------------------------------

    async load() {
        this.state.busy = true;
        this.state.error = "";
        try {
            const data = await this.orm.call("odin.pnl.report", "get_report", [this.state.options]);
            this.state.options = data.options;
            this.state.rows = data.rows;
            delete data.rows;
            this.state.data = data;
            writePrefs(data.options);
        } catch (error) {
            this.state.error = error.data?.message || error.message || String(error);
        } finally {
            this.state.loading = false;
            this.state.busy = false;
        }
    }

    async changeOptions(patch) {
        const options = { ...this.state.options, ...patch };
        if (patch.date) {
            options.date = { ...patch.date };
        }
        this.state.options = options;
        this.state.editing = null;
        await this.load();
    }

    async replaceOptions(options) {
        this.state.options = options;
        this.state.editing = null;
        this.state.panel = null;
        await this.load();
    }

    async export(fmt) {
        this.state.exporting = true;
        try {
            await download({
                url: "/odin_account_pnl/export",
                data: { options: JSON.stringify(this.state.options), fmt },
            });
        } finally {
            this.state.exporting = false;
        }
    }

    async createBudget(name) {
        try {
            const budgetId = await this.orm.call("odin.pnl.report", "create_budget", [this.state.options, name]);
            await this.changeOptions({ budget_id: budgetId });
        } catch (error) {
            this.notification.add(error.data?.message || String(error), { type: "danger" });
        }
    }

    // ------------------------------------------------------------------
    // Rows: unfolding, modes, pages
    // ------------------------------------------------------------------

    get columns() {
        return this.state.data?.columns || [];
    }

    get hasGrowth() {
        return this.columns.some((column) => column.group === "comparison");
    }

    /** A Balance column while an account shows its ledger (running balance). */
    get showRunning() {
        return this.state.rows.some((row) => row.type === "entry" && row.running !== null && row.running !== undefined);
    }

    get currencyDecimals() {
        return this.state.data?.currency.decimals ?? 2;
    }

    rowByKey(key) {
        return this.state.rows.find((row) => row.key === key);
    }

    descendantsOf(key) {
        const prefix = `${key}/`;
        return this.state.rows.filter((row) => row.key.startsWith(prefix));
    }

    removeDescendants(key) {
        const prefix = `${key}/`;
        this.state.rows = this.state.rows.filter((row) => !row.key.startsWith(prefix));
        const options = this.state.options;
        options.unfolded = options.unfolded.filter((k) => k !== key && !k.startsWith(prefix));
        for (const k of Object.keys(options.modes)) {
            if (k.startsWith(prefix)) {
                delete options.modes[k];
            }
        }
    }

    async toggle(row) {
        if (!row.unfoldable) {
            return;
        }
        if (row.unfolded) {
            this.removeDescendants(row.key);
            row.unfolded = false;
            return;
        }
        await this.unfold(row, row.mode);
    }

    async unfold(row, mode, extra = {}) {
        this.state.busy = true;
        try {
            const result = await this.orm.call("odin.pnl.report", "get_children", [
                this.state.options,
                row.key,
                mode || null,
                extra.offset || 0,
                extra.running || 0,
                extra.showAll || false,
            ]);
            this.removeDescendants(row.key);
            const index = this.state.rows.findIndex((r) => r.key === row.key);
            this.state.rows.splice(index + 1, 0, ...result.rows);
            row.unfolded = true;
            row.mode = result.mode;
            const options = this.state.options;
            if (!options.unfolded.includes(row.key)) {
                options.unfolded.push(row.key);
            }
            options.modes[row.key] = result.mode;
        } catch (error) {
            this.notification.add(error.data?.message || String(error), { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }

    async setMode(row, mode) {
        this.closeMenu();
        await this.unfold(row, mode);
    }

    async audit(row, column) {
        if (!row.unfoldable || row.type === "entry") {
            return;
        }
        await this.unfold(row, `audit:${column.key}`);
    }

    async loadMore(moreRow) {
        const parent = this.rowByKey(moreRow.parent);
        this.state.busy = true;
        try {
            const result = await this.orm.call("odin.pnl.report", "get_children", [
                this.state.options,
                parent.key,
                parent.mode,
                moreRow.offset,
                moreRow.running || 0,
                false,
            ]);
            const index = this.state.rows.findIndex((r) => r.key === moreRow.key);
            this.state.rows.splice(index, 1, ...result.rows);
        } finally {
            this.state.busy = false;
        }
    }

    async showAll(othersRow) {
        const parent = this.rowByKey(othersRow.parent);
        await this.unfold(parent, parent.mode, { showAll: true });
    }

    // ------------------------------------------------------------------
    // What the table shows
    // ------------------------------------------------------------------

    /**
     * Rows in display order: siblings sorted when a column header was
     * clicked (accounts and contributors, never journal items), filtered by
     * the search box (a match keeps its parents).
     */
    get visibleRows() {
        let rows = this.state.rows;
        if (this.state.sort) {
            rows = this.sortRows(rows);
        }
        const search = this.state.search.trim().toLowerCase();
        if (!search) {
            return rows;
        }
        const keep = new Set();
        for (const row of rows) {
            const text = `${row.code || ""} ${row.name || ""}`.toLowerCase();
            if (text.includes(search)) {
                let key = row.key;
                while (key) {
                    keep.add(key);
                    key = this.rowByKey(key)?.parent;
                }
            }
        }
        return rows.filter((row) => keep.has(row.key) || ["formula", "heading"].includes(row.type));
    }

    sortRows(rows) {
        const { key, direction } = this.state.sort;
        const children = new Map();
        for (const row of rows) {
            const parent = row.parent || "";
            if (!children.has(parent)) {
                children.set(parent, []);
            }
            children.get(parent).push(row);
        }
        const sortable = (list) => list.length > 1 && list.every((r) => ["account", "group", "breakdown"].includes(r.type));
        const flatten = (parent) => {
            let list = children.get(parent) || [];
            if (sortable(list)) {
                list = [...list].sort((a, b) => direction * ((b.values[key] || 0) - (a.values[key] || 0)));
            }
            const out = [];
            for (const row of list) {
                out.push(row);
                out.push(...flatten(row.key));
            }
            return out;
        };
        return flatten("");
    }

    toggleSort(column) {
        const sort = this.state.sort;
        if (!sort || sort.key !== column.key) {
            this.state.sort = { key: column.key, direction: 1 };
        } else if (sort.direction === 1) {
            this.state.sort = { key: column.key, direction: -1 };
        } else {
            this.state.sort = null;
        }
    }

    sortIcon(column) {
        const sort = this.state.sort;
        if (!sort || sort.key !== column.key) {
            return "";
        }
        return sort.direction === 1 ? "fa-sort-amount-desc" : "fa-sort-amount-asc";
    }

    fmt(value) {
        const options = this.state.options;
        return formatAmount(value, {
            scale: options.scale,
            decimals: options.decimals,
            currencyDecimals: this.currencyDecimals,
            parentheses: options.negative_parentheses,
        });
    }

    rowClass(row) {
        return {
            [`o_odin_pnl_row_${row.type}`]: true,
            o_odin_pnl_bold: row.bold,
            o_odin_pnl_result: row.is_result,
            o_odin_pnl_unfolded: row.unfolded,
            o_odin_pnl_focus: row.key === this.state.focusKey,
            o_odin_pnl_peeked: this.state.panel?.row?.key === row.key,
            o_odin_pnl_draft: row.draft,
            // Split by something else than its accounts or items: the "By"
            // chip stays visible, so the split reads without hovering.
            o_odin_pnl_split: Boolean(row.unfolded && row.mode && !["accounts", "entries"].includes(row.mode)),
        };
    }

    indent(row) {
        return `padding-left: ${0.75 + (row.level || 0) * 1.1}rem`;
    }

    growthClass(row) {
        if (row.growth === null || row.growth === undefined || Math.abs(row.growth) < 0.05) {
            return "text-muted";
        }
        return row.growth_good ? "o_odin_pnl_good" : "o_odin_pnl_bad";
    }

    budgetPctClass(row, column) {
        const pct = row.budget_pct?.[column.key];
        if (pct === null || pct === undefined) {
            return "text-muted";
        }
        const good = row.green_on_positive ? pct >= 100 : pct <= 100;
        return good ? "o_odin_pnl_good" : "o_odin_pnl_bad";
    }

    shareStyle(row) {
        return `width: ${Math.min(Math.max(row.share || 0, 0), 100)}%`;
    }

    onCellClick(ev, row, column) {
        if (this.canAudit(row)) {
            ev.stopPropagation();
            this.state.focusKey = row.key;
            this.audit(row, column);
        }
    }

    canAudit(row) {
        return row.unfoldable && ["line", "account", "group", "breakdown"].includes(row.type);
    }

    modeLabel(row) {
        if (!row.mode || row.mode === "accounts" || row.mode === "entries") {
            return _t("By");
        }
        if (row.mode.startsWith("audit:")) {
            return _t("Items of one column");
        }
        const found = modesFor(row, this.state.data.analytic_plans).find(([key]) => key === row.mode);
        return found ? found[1] : _t("By");
    }

    // ------------------------------------------------------------------
    // Menus (one shared menu instead of a dropdown per row)
    // ------------------------------------------------------------------

    openMenu(ev, row, kind) {
        ev.stopPropagation();
        this.openMenuAt(ev.currentTarget, row, kind);
    }

    /** The shared row menu, placed under ``anchor`` (a row's button). */
    openMenuAt(anchor, row, kind, fromKeyboard = false) {
        const rect = anchor.getBoundingClientRect();
        const rootRect = this.rootRef.el.getBoundingClientRect();
        this.state.focusKey = row.key;
        this.state.menu = {
            kind,
            key: row.key,
            top: rect.bottom - rootRect.top + 2,
            left: Math.min(rect.left - rootRect.left, rootRect.width - 260),
            fromKeyboard,
        };
    }

    closeMenu() {
        this.state.menu = null;
        this.rootRef.el?.focus();
    }

    onWindowClick(ev) {
        if (this.state.menu && !ev.target.closest(".o_odin_pnl_rowmenu")) {
            this.state.menu = null;
        }
    }

    get menuRow() {
        return this.state.menu && this.rowByKey(this.state.menu.key);
    }

    get menuModes() {
        const row = this.menuRow;
        return row ? modesFor(row, this.state.data.analytic_plans) : [];
    }

    get menuActions() {
        const row = this.menuRow;
        if (!row) {
            return [];
        }
        const actions = [];
        if (row.aml_id) {
            actions.push({ label: _t("View Journal Entry"), icon: "fa-external-link", run: () => this.openMove(row.aml_id) });
            actions.push({ label: _t("Peek"), icon: "fa-eye", run: () => this.openPanel("peek", row) });
        }
        if (["line", "account", "group", "breakdown"].includes(row.type) && row.unfoldable) {
            actions.push({ label: _t("Journal Items"), icon: "fa-list", run: () => this.openItems(row) });
        }
        if (sidePanelRegistry.contains("notes") && sidePanelRegistry.get("notes").isAvailable(row)) {
            actions.push({ label: _t("Annotate"), icon: "fa-sticky-note-o", run: () => this.openPanel("notes", row) });
        }
        for (const [, item] of lineActionRegistry.getEntries()) {
            if (!item.isAvailable || item.isAvailable(row)) {
                actions.push({ label: item.label, icon: item.icon, sequence: item.sequence, run: () => item.run(row, this) });
            }
        }
        return actions;
    }

    runMenuAction(item) {
        this.closeMenu();
        item.run();
    }

    // ------------------------------------------------------------------
    // Opening things
    // ------------------------------------------------------------------

    async openMove(amlId) {
        const action = await this.orm.call("odin.pnl.report", "action_open_move", [amlId]);
        await this.action.doAction(action);
    }

    async openItems(row, column) {
        const action = await this.orm.call("odin.pnl.report", "action_open_items", [
            this.state.options,
            row.key,
            column ? column.key : null,
        ]);
        await this.action.doAction(action);
    }

    openPanel(tab, row) {
        this.state.panel = { tab, row };
        this.state.focusKey = row.key;
    }

    selectTab(tab) {
        this.state.panel = { ...this.state.panel, tab };
    }

    closePanel() {
        this.state.panel = null;
    }

    onRowClick(row) {
        this.state.focusKey = row.key;
        if (row.type === "entry") {
            this.openPanel("peek", row);
        } else if (row.type === "more") {
            this.loadMore(row);
        } else if (row.type === "others") {
            this.showAll(row);
        } else {
            this.toggle(row);
        }
    }

    entrySiblings(row) {
        return this.state.rows.filter((r) => r.type === "entry" && r.parent === row.parent);
    }

    peekSibling(row, delta) {
        const siblings = this.entrySiblings(row);
        const index = siblings.findIndex((r) => r.key === row.key);
        const next = siblings[index + delta];
        if (next) {
            this.openPanel(this.state.panel?.tab || "peek", next);
            this.scrollIntoView(next.key);
        }
    }

    scrollIntoView(key, block = "nearest") {
        const el = this.scrollerRef.el?.querySelector(`[data-key="${CSS.escape(key)}"]`);
        el?.scrollIntoView({ block });
    }

    // ------------------------------------------------------------------
    // Budget cells
    // ------------------------------------------------------------------

    canEditBudget(row) {
        return this.state.data?.can_edit_budget && row.type === "account" && row.editable_budget;
    }

    editBudget(row, column) {
        if (this.canEditBudget(row)) {
            this.state.editing = { key: row.key, column: column.key };
        }
    }

    isEditing(row, column) {
        return this.state.editing?.key === row.key && this.state.editing?.column === column.key;
    }

    async onBudgetKey(ev, row, column) {
        if (ev.key === "Escape") {
            this.state.editing = null;
        } else if (ev.key === "Enter") {
            await this.saveBudget(ev.target.value, row, column);
        }
    }

    async saveBudget(text, row, column) {
        if (!this.isEditing(row, column)) {
            return;
        }
        this.state.editing = null;
        const amount = parseFloat(String(text).replace(/[^0-9.\-]/g, "")) || 0;
        try {
            await this.orm.call("odin.pnl.report", "set_budget_amount", [
                this.state.options,
                row.key,
                column.key,
                amount * (this.state.options.scale || 1),
            ]);
            await this.load();
        } catch (error) {
            this.notification.add(error.data?.message || String(error), { type: "danger" });
        }
    }

    // ------------------------------------------------------------------
    // Keyboard
    // ------------------------------------------------------------------

    onKeydown(ev) {
        if (["INPUT", "TEXTAREA", "SELECT"].includes(ev.target.tagName)) {
            return;
        }
        // Ctrl/Cmd/Alt combinations belong to the browser and to Odoo (Ctrl+K
        // opens the command palette); Shift is allowed for "?".
        if (ev.ctrlKey || ev.metaKey || ev.altKey) {
            return;
        }
        // Keys inside the row menu, and the arrows while it is open, even
        // when it was opened with the mouse, belong to the menu.
        if (ev.target.closest(".o_odin_pnl_rowmenu") ||
            (this.state.menu && (ev.key === "ArrowDown" || ev.key === "ArrowUp"))) {
            this.onMenuKeydown(ev);
            return;
        }
        const rows = this.visibleRows.filter((row) => row.type !== "heading");
        const index = rows.findIndex((row) => row.key === this.state.focusKey);
        const row = rows[index];
        switch (ev.key) {
            case "ArrowDown":
            case "ArrowUp": {
                const next = rows[Math.min(Math.max(index + (ev.key === "ArrowDown" ? 1 : -1), 0), rows.length - 1)];
                if (next) {
                    this.state.focusKey = next.key;
                    this.scrollIntoView(next.key);
                }
                break;
            }
            case "ArrowRight":
                if (row && row.unfoldable && !row.unfolded) {
                    this.toggle(row);
                }
                break;
            case "ArrowLeft":
                if (row && row.unfolded) {
                    this.toggle(row);
                } else if (row && row.parent) {
                    this.state.focusKey = row.parent;
                    this.scrollIntoView(row.parent);
                }
                break;
            case "Enter":
                if (row) {
                    this.onRowClick(row);
                }
                break;
            case "j":
            case "k":
                if (this.state.panel?.row?.type === "entry") {
                    this.peekSibling(this.state.panel.row, ev.key === "j" ? 1 : -1);
                }
                break;
            case "/":
                ev.preventDefault();
                this.rootRef.el.querySelector(".o_odin_pnl_search input")?.focus();
                break;
            case "b": {
                const button = row && this.scrollerRef.el?.querySelector(
                    `tr[data-key="${CSS.escape(row.key)}"] .o_odin_pnl_by`);
                if (!button) {
                    return;
                }
                this.openMenuAt(button, row, "by", true);
                break;
            }
            case "?":
                this.openShortcuts();
                break;
            case "Escape":
                // One thing at a time: the menu first, then the side panel.
                if (this.state.menu) {
                    this.state.menu = null;
                } else {
                    this.state.panel = null;
                }
                break;
            default: {
                const shortcut = lineActionRegistry
                    .getEntries()
                    .find(([, item]) => item.hotkey === ev.key && (!item.isAvailable || (row && item.isAvailable(row))));
                if (shortcut && row) {
                    shortcut[1].run(row, this);
                } else {
                    return;
                }
            }
        }
        ev.preventDefault();
    }

    /** Keys inside the row menu: arrows move between its items, Escape closes
     * it; Enter and Space are left to the focused button. */
    onMenuKeydown(ev) {
        if (ev.key === "Escape") {
            ev.preventDefault();
            this.closeMenu();
            return;
        }
        if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") {
            return;
        }
        ev.preventDefault();
        const items = [...this.rootRef.el.querySelectorAll(".o_odin_pnl_rowmenu .dropdown-item")];
        const step = ev.key === "ArrowDown" ? 1 : -1;
        const index = items.indexOf(document.activeElement);
        // Not in the menu yet: down enters at the top, up at the bottom.
        const next = index === -1
            ? items.at(step === 1 ? 0 : -1)
            : items[(index + step + items.length) % items.length];
        next?.focus();
    }

    /** Line actions that declare a key (the AI module's Explain, for one). */
    get lineActionShortcuts() {
        return lineActionRegistry
            .getEntries()
            .filter(([, item]) => item.hotkey)
            .map(([, item]) => ({ key: item.hotkey, label: item.label }));
    }

    openShortcuts() {
        this.dialog.add(
            PnlShortcutsDialog,
            { lineActions: this.lineActionShortcuts },
            { onClose: () => this.rootRef.el?.focus() }
        );
    }

    dismissTip() {
        this.state.tipDismissed = true;
        try {
            browser.localStorage.setItem(TIP_KEY, "1");
        } catch {
            // Storage blocked: the tip shows again next time.
        }
    }

    onSearch(value) {
        this.state.search = value;
        if (value.trim() && !this.state.options.unfold_all) {
            this.changeOptions({ unfold_all: true });
        }
    }

    editLayout() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "odin.pnl.layout",
            res_id: this.state.options.layout_id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("odin_account_pnl.report", PnlAction);
