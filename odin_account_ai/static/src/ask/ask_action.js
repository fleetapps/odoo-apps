import { Component, onMounted, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { downloadFile } from "@web/core/network/download";
import { ConnectionLostError } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { escapeRegExp } from "@web/core/utils/strings";
import { Layout } from "@web/search/layout";
import { useSetupAction } from "@web/search/action_hook";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const HANDLE = /\[(E\d+)\]/g;
const NUMERIC = /^[-+(]?[\d\s.,]+%?\)?$/;
// A currency sign written beside a figure ("$1,234", "1,234 €").
const CURRENCY_SIGN = /^[$€£¥₹]\s?|\s?[$€£¥₹]$/;
// A table cell meaning "no figure".
const NO_FIGURE = /^(-|–|—|n\/a)$/i;
// After a lost connection the same step is sent again: a step whose answer
// was already paid for reuses it (the step key in odin.ai.llm.call).
const RETRY_DELAYS = [2000, 4000, 8000];
const BUSY_DELAY = 800;
const KIND_ICONS = { pnl: "fa-table", list: "fa-list", record: "fa-file-text-o" };
const TOTAL_LABEL = /^(grand\s+)?totals?:?$/i;
const TOTAL_START = /^(grand\s+)?totals?\b/i;

/** Plain text with [E3] references turned into chips, **bold** kept. */
export function splitText(text, evidence) {
    const parts = [];
    let last = 0;
    const push = (chunk) => {
        chunk.split(/(\*\*[^*]+\*\*)/).forEach((piece) => {
            if (!piece) {
                return;
            }
            if (piece.startsWith("**") && piece.endsWith("**") && piece.length > 4) {
                parts.push({ text: piece.slice(2, -2), bold: true });
            } else {
                parts.push({ text: piece });
            }
        });
    };
    for (const match of (text || "").matchAll(HANDLE)) {
        push(text.slice(last, match.index));
        if (evidence[match[1]]) {
            parts.push({ ref: match[1] });
        }
        last = match.index + match[0].length;
    }
    push((text || "").slice(last));
    return parts;
}

/** Text without its references and bold marks, for copies and figures. */
export function plainText(text) {
    return (text || "").replace(HANDLE, "").replace(/\*\*/g, "").replace(/\s+([.,;:])/g, "$1").trim();
}

/** A figure as a number: "1,234.50", "(300)", "-12.5%"; null for text. */
function toNumber(text) {
    const negative = /^\(.*\)$/.test(text) || text.startsWith("-");
    const value = parseFloat(text.replace(/[^\d.]/g, ""));
    return Number.isNaN(value) ? null : negative ? -value : value;
}

/**
 * Ask the Ledger: questions about the books, answered from them.
 *
 * The page drives each conversation one step at a time (ask_step), so each
 * request stays short and the steps show as they happen ("Reading the P&L
 * for Sep 2026"). Every figure in an answer cites the tool rows it came
 * from; a chip opens that row in the P&L or the document itself.
 *
 * Each running conversation has its own step loop. A loop updates the screen
 * only while its conversation is the one shown, so opening another one, or
 * starting a new question, never brings back the one being answered: it
 * finishes in the background and its History entry follows.
 */
export class AskAction extends Component {
    static template = "odin_account_ai.AskAction";
    static components = { Layout };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.threadRef = useRef("thread");
        this.inputRef = useRef("input");
        const restored = this.props.state?.odinAsk;
        const params = this.props.action.params || {};
        // "Ask about this line" (P&L) and Ctrl+K open the page with a draft
        // to complete, never sent on the user's behalf.
        const draft = !restored && !params.question && params.draft;
        this.state = useState({
            boot: null,
            thread: null,
            input: draft || "",
            sending: false,
            // By conversation id: a Stop waiting for the step in flight, and a
            // conversation whose loop ended on an error while it still runs.
            stopping: {},
            lost: {},
            history: restored?.history ?? !this.env.isSmall,
            openSteps: {},
            sort: {},
            // What the page the question came from showed, sent with it.
            context: draft ? params.context || null : null,
        });
        this.alive = true;
        this.loops = new Set();
        this.stopRequests = new Set();
        this.refNumbers = new WeakMap();
        // Follow the newest answer only while the reader is already at the
        // bottom, or right after their own action (asking, opening): reading
        // an earlier answer must not snap back on every progress tick.
        this.stickToBottom = true;
        useSetupAction({
            getLocalState: () => ({
                odinAsk: { conversationId: this.state.thread?.id || false, history: this.state.history },
            }),
        });
        onWillStart(async () => {
            this.state.boot = await this.orm.call("odin.ai.conversation", "ask_bootstrap", []);
            if (restored?.conversationId) {
                await this.open(restored.conversationId);
            }
        });
        onMounted(() => {
            if (!restored && params.question) {
                this.ask(params.question, params.context);
            } else {
                this.focusInput(Boolean(draft));
            }
        });
        onWillUnmount(() => {
            this.alive = false;
        });
        useEffect(
            () => {
                const el = this.threadRef.el;
                if (el && this.stickToBottom) {
                    el.scrollTop = el.scrollHeight;
                }
            },
            () => [this.state.thread?.id, this.state.thread?.turns?.length, this.lastTurn?.progress?.length,
                this.state.thread?.state]
        );
    }

    /** Within a screen's worth of the end counts as "at the bottom". */
    onThreadScroll(ev) {
        const el = ev.target;
        this.stickToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    }

    get lastTurn() {
        return this.state.thread?.turns?.at(-1);
    }

    get running() {
        return this.state.thread?.state === "running";
    }

    get stopping() {
        const id = this.state.thread?.id;
        return Boolean(id && this.state.stopping[id]);
    }

    get lost() {
        const id = this.state.thread?.id;
        return Boolean(id && this.state.lost[id]);
    }

    /** Why the step loop of the conversation shown gave up. */
    get lostLabel() {
        return this.state.lost[this.state.thread?.id] === "connection"
            ? _t("The connection was lost while the answer was being prepared.")
            : _t("Preparing the answer failed.");
    }

    /** The server caps a question at max_steps and says where it has got to;
     * "Thinking…" alone leaves the reader unable to tell progress from a hang. */
    get progressLabel() {
        if (this.stopping) {
            return _t("Stopping after this step…");
        }
        const thread = this.state.thread;
        if (!thread?.step || !thread?.max_steps) {
            return _t("Thinking…");
        }
        return _t("Thinking… step %(step)s of %(max)s", { step: thread.step, max: thread.max_steps });
    }

    get placeholder() {
        const state = this.state.thread?.state;
        if (this.state.context) {
            // A question carrying a P&L line's context always starts afresh.
            return _t("Ask about this line: it starts a new conversation…");
        }
        if (state === "done") {
            return _t("Ask a follow-up…");
        }
        if (state === "error" || state === "stopped") {
            return _t("Ask something else: it starts a new conversation…");
        }
        return _t("Ask about revenue, costs, customers, suppliers…");
    }

    get stopLabel() {
        return this.stopping ? _t("Stopping…") : _t("Stop this question");
    }

    get setupHint() {
        if (this.state.boot?.setup === "budget") {
            return _t("Ask an administrator to raise the monthly budget, or wait until next month.");
        }
        return _t("Ask an administrator to set up Accounting AI.");
    }

    get contextLabel() {
        const context = this.state.context || {};
        const period = context.comparison
            ? _t("%(period)s vs %(comparison)s", { period: context.period, comparison: context.comparison })
            : context.period;
        return [context.line, period].filter(Boolean).join(" · ");
    }

    stepsLabel(turn) {
        const count = turn.progress.filter((step) => typeof step === "string").length;
        if (!count) {
            return _t("Notes");
        }
        return count === 1 ? _t("Read the books in 1 step") : _t("Read the books in %s steps", count);
    }

    /** Progress entries: the tools' labels, and the model's notes between
     * them (progress updates, when the conversation asked for them). */
    stepItems(turn) {
        return turn.progress.map((step) =>
            typeof step === "string" ? { text: step, note: false } : { text: step.note, note: true }
        );
    }

    get canAsk() {
        return !this.state.boot?.ready && !this.running && !this.state.sending && this.state.input.trim();
    }

    // ------------------------------------------------------------------
    // Asking
    // ------------------------------------------------------------------

    /** Ask ``question``: a follow-up in the conversation shown when it is
     * answered, else a new conversation. ``context`` (the P&L line's) goes
     * with the composer's question; suggestions and starters pass none. */
    async ask(question, context = this.state.context) {
        question = (question || "").trim();
        if (!question || this.running || this.state.sending) {
            return;
        }
        // A suggestion or a starter leaves what is being typed alone.
        const fromComposer = question === this.state.input.trim();
        this.state.sending = true;
        this.stickToBottom = true;
        let id;
        try {
            const thread = this.state.thread;
            if (thread && thread.state === "done" && !context) {
                this.state.thread = await this.orm.call("odin.ai.conversation", "ask_followup", [
                    [thread.id],
                    question,
                ]);
                id = thread.id;
            } else {
                id = await this.orm.call("odin.ai.conversation", "ask_start", [question, context]);
                this.state.thread = await this.orm.call("odin.ai.conversation", "get_thread", [[id]]);
                this.state.boot.recent.unshift({ id, name: question, state: "running" });
                if (context && context === this.state.context) {
                    this.state.context = null;
                }
            }
            if (fromComposer) {
                this.state.input = "";
            }
        } catch (error) {
            this.notify(error);
            return;
        } finally {
            this.state.sending = false;
        }
        this.run(id);
    }

    /** Step conversation ``id`` until it is answered, failed or stopped.
     *
     * A "busy" reply means another request holds the conversation (a double
     * click, Odoo replaying a request): wait and look again. A Stop is sent
     * here, between two steps, when the row is free: sent while a step waits
     * on the API it could only wait for that step. */
    async run(id) {
        if (!id || this.loops.has(id)) {
            return;
        }
        this.loops.add(id);
        delete this.state.lost[id];
        let failures = 0;
        try {
            while (this.alive) {
                const method = this.stopRequests.has(id) ? "ask_stop" : "ask_step";
                let thread;
                try {
                    thread = await this.orm.call("odin.ai.conversation", method, [[id]]);
                } catch (error) {
                    if (error instanceof ConnectionLostError && failures < RETRY_DELAYS.length) {
                        await this.sleep(RETRY_DELAYS[failures++]);
                        continue;
                    }
                    await this.lose(id, error);
                    return;
                }
                failures = 0;
                if (!this.alive) {
                    return;
                }
                this.show(thread);
                if (thread.state !== "running") {
                    return;
                }
                if (thread.busy) {
                    await this.sleep(BUSY_DELAY);
                }
            }
        } finally {
            this.loops.delete(id);
            // A Stop pressed while the loop was giving up waits for Stop or
            // Resume on the "lost" line; otherwise it is settled.
            if (!this.state.lost[id]) {
                this.stopRequests.delete(id);
                delete this.state.stopping[id];
            }
            if (this.alive && this.state.thread?.id === id) {
                this.focusInput();
            }
        }
    }

    /** The loop of ``id`` gave up on an error: say so, and if the question is
     * still running server side, offer to resume it rather than spin. */
    async lose(id, error) {
        if (error instanceof ConnectionLostError) {
            this.notification.add(_t("The connection to the server was lost."), { type: "warning" });
        } else {
            this.notify(error);
        }
        let thread = null;
        try {
            thread = await this.orm.call("odin.ai.conversation", "get_thread", [[id]]);
        } catch {
            // Still unreachable: Resume will try again.
        }
        if (thread) {
            this.show(thread);
        }
        if (this.alive && (!thread || thread.state === "running")) {
            this.state.lost[id] = error instanceof ConnectionLostError ? "connection" : "error";
        }
    }

    /** Take a conversation's latest state: on screen if it is the one shown,
     * and in History in any case. */
    show(thread) {
        if (this.state.thread?.id === thread.id) {
            this.state.thread = thread;
        }
        const recent = this.state.boot.recent.find((item) => item.id === thread.id);
        if (recent) {
            recent.state = thread.state;
        }
    }

    /** Stop the question on screen once the step in flight has landed. */
    stop() {
        const id = this.state.thread?.id;
        // Pressed again with no loop running (the connection was lost): send it.
        if (!id || !this.running || (this.state.stopping[id] && this.loops.has(id))) {
            return;
        }
        this.state.stopping[id] = true;
        this.stopRequests.add(id);
        this.run(id);
    }

    /** Go on with the question: Resume overrides a Stop still waiting. */
    resume() {
        const id = this.state.thread?.id;
        this.stopRequests.delete(id);
        delete this.state.stopping[id];
        this.run(id);
    }

    async retry() {
        const id = this.state.thread.id;
        this.stickToBottom = true;
        try {
            this.show(await this.orm.call("odin.ai.conversation", "ask_retry", [[id]]));
        } catch (error) {
            this.notify(error);
            return;
        }
        this.run(id);
    }

    /** The question could not be retried in place: put it in the composer of
     * a new conversation. */
    askAgain() {
        const question = this.lastTurn?.question || "";
        this.newConversation();
        this.state.input = question;
    }

    async open(id) {
        this.stickToBottom = true;
        let thread;
        try {
            thread = await this.orm.call("odin.ai.conversation", "get_thread", [[id]]);
        } catch (error) {
            this.notify(error);
            return;
        }
        this.state.thread = thread;
        if (this.env.isSmall) {
            this.state.history = false;
        }
        if (thread.state === "running") {
            this.run(id);
        }
    }

    newConversation() {
        this.state.thread = null;
        this.state.input = "";
        this.state.context = null;
        this.stickToBottom = true;
        this.focusInput();
    }

    openSettings() {
        this.action.doAction("account.action_account_config");
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
            ev.preventDefault();
            this.ask(this.state.input);
        }
    }

    focusInput(atEnd = false) {
        const el = this.inputRef.el;
        if (!el) {
            return;
        }
        el.focus();
        if (atEnd) {
            el.setSelectionRange(el.value.length, el.value.length);
        }
    }

    sleep(delay) {
        return new Promise((resolve) => browser.setTimeout(resolve, delay));
    }

    // ------------------------------------------------------------------
    // Answers
    // ------------------------------------------------------------------

    parts(text) {
        return splitText(text, this.state.thread?.evidence || {});
    }

    /** Text without its references, for a KPI figure. */
    plain(text) {
        return plainText(text);
    }

    label(ref) {
        return this.state.thread?.evidence?.[ref]?.label || ref;
    }

    /** The companies an answer's figures cover: those selected while it was
     * worked out (the conversation's company for answers from before). */
    companiesLabel(turn) {
        return turn.companies?.length ? turn.companies.join(", ") : this.state.thread.company;
    }

    kindIcon(ref) {
        return KIND_ICONS[this.state.thread?.evidence?.[ref]?.kind] || KIND_ICONS.record;
    }

    /** Chips are numbered 1, 2, 3… within each answer, in reading order (KPI
     * tiles first, as shown): the handle behind a chip is conversation-wide. */
    refNumber(turn, ref) {
        const answer = turn.answer;
        let numbers = this.refNumbers.get(answer);
        if (!numbers) {
            numbers = new Map();
            const add = (handle) => numbers.has(handle) || numbers.set(handle, numbers.size + 1);
            const scan = (text) => [...(text || "").matchAll(HANDLE)].forEach((match) => add(match[1]));
            const blocks = answer.blocks || [];
            for (const block of [...blocks.filter((b) => b.type === "kpi"), ...blocks.filter((b) => b.type !== "kpi")]) {
                scan(block.text);
                (block.items || []).forEach(scan);
                (block.rows || []).forEach((row) => row.forEach(scan));
                (block.evidence || []).forEach(add);
            }
            this.refNumbers.set(answer, numbers);
        }
        return numbers.get(ref) || "?";
    }

    /** A table cell without its references, and without the currency when
     * it is written beside a figure ("KES 1,234", "$1,234"). */
    figureText(cell) {
        let text = plainText(cell);
        const code = this.state.thread?.currency;
        if (code) {
            text = text.replace(new RegExp(`^${escapeRegExp(code)}\\s?|\\s?${escapeRegExp(code)}$`), "");
        }
        return text.replace(CURRENCY_SIGN, "").trim();
    }

    /** A table cell as a number for sorting, or null. */
    cellNumber(cell) {
        return toNumber(this.figureText(cell));
    }

    isNumeric(cell) {
        return NUMERIC.test(this.figureText(cell));
    }

    /** A table column whose cells are all figures (or "—", "n/a") is
     * right-aligned, header included, and sorts by value. */
    numericColumn(block, index) {
        const rows = block.rows || [];
        const cells = rows.map((row) => this.figureText(row[index])).filter((text) => text && !NO_FIGURE.test(text));
        return cells.length > 0 && cells.every((text) => NUMERIC.test(text));
    }

    /** References of a block not already shown inline in its text. */
    extraRefs(block) {
        const inline = new Set();
        for (const text of [block.text, ...(block.items || []), ...(block.rows || []).flat()]) {
            for (const match of (text || "").matchAll(HANDLE)) {
                inline.add(match[1]);
            }
        }
        return [...new Set(block.evidence || [])].filter((ref) => !inline.has(ref));
    }

    async openEvidence(ref) {
        try {
            const action = await this.orm.call("odin.ai.conversation", "open_evidence", [
                [this.state.thread.id],
                ref,
            ]);
            await this.action.doAction(action);
        } catch (error) {
            this.notify(error);
        }
    }

    toggleSteps(index) {
        this.state.openSteps[index] = !this.state.openSteps[index];
    }

    async feedback(index, value) {
        const thread = this.state.thread;
        const turn = thread.turns[index];
        const next = turn.feedback === value ? false : value;
        try {
            await this.orm.call("odin.ai.conversation", "set_feedback", [[thread.id], next, index]);
        } catch (error) {
            this.notify(error);
            return;
        }
        turn.feedback = next;
    }

    async copy(turn) {
        const lines = [];
        for (const block of turn.answer.blocks) {
            if (block.type === "kpi") {
                lines.push(`${plainText(block.text)}: ${plainText(block.value)}`);
            } else if (block.type === "bullets") {
                lines.push(...(block.items || []).map((item) => `• ${plainText(item)}`));
            } else if (block.type === "table") {
                lines.push(this.tableLines(block, block.rows || [], "\t").join("\n"));
            } else {
                lines.push(plainText(block.text));
            }
            lines.push("");
        }
        await this.writeClipboard(lines.join("\n").trim(), _t("Answer copied"));
    }

    // ------------------------------------------------------------------
    // Tables: sort, copy, CSV
    // ------------------------------------------------------------------

    tableKey(turnIndex, blockIndex) {
        return `${this.state.thread.id}:${turnIndex}:${blockIndex}`;
    }

    /** The table's closing total row, or null: its last row, labelled
     * "Total" or "Grand total", or starting so ("Total revenue") with figures
     * that add up the rows above. A supplier called "Total Kenya" is a row
     * like the others. */
    tableTotal(block) {
        const rows = block.rows || [];
        const last = rows.at(-1);
        if (rows.length < 2 || !last) {
            return null;
        }
        // The label is the first filled cell: a code column may be left empty.
        const label = plainText(last.find((cell) => plainText(cell)) || "");
        const word = _t("Total").toLowerCase();
        const lower = label.toLowerCase().replace(/:$/, "");
        if (TOTAL_LABEL.test(label) || lower === word || lower === _t("Grand total").toLowerCase()) {
            return last;
        }
        if (!TOTAL_START.test(label) && !lower.startsWith(`${word} `)) {
            return null;
        }
        // Every figure column must add up (percentages aside: a growth does
        // not), so that one small count matching by chance is not enough.
        const body = rows.slice(0, -1);
        const headers = block.columns || [];
        let added = 0;
        for (let index = 1; index < last.length; index++) {
            const total = this.cellNumber(last[index]);
            const percent = (headers[index] || "").includes("%") || this.figureText(last[index]).endsWith("%");
            if (total === null || percent || !this.numericColumn(block, index)) {
                continue;
            }
            const values = body.map((row) => this.cellNumber(row[index])).filter((value) => value !== null);
            const sum = values.reduce((a, b) => a + b, 0);
            // Figures in an answer are rounded: half a unit, or 0.5%, of slack.
            if (!values.length || Math.abs(sum - total) > Math.max(Math.abs(total) * 0.005, 0.5)) {
                return null;
            }
            added++;
        }
        return added ? last : null;
    }

    /** The rows in the chosen order, the total row kept at the bottom. */
    tableRows(turnIndex, blockIndex, block) {
        const rows = block.rows || [];
        const total = this.tableTotal(block);
        const body = rows.filter((row) => row !== total);
        const sort = this.state.sort[this.tableKey(turnIndex, blockIndex)];
        if (sort) {
            const numeric = this.numericColumn(block, sort.column);
            const direction = sort.direction === "asc" ? 1 : -1;
            body.sort((a, b) => {
                if (numeric) {
                    return ((this.cellNumber(a[sort.column]) ?? -Infinity) - (this.cellNumber(b[sort.column]) ?? -Infinity)) *
                        direction;
                }
                return plainText(a[sort.column]).localeCompare(plainText(b[sort.column])) * direction;
            });
        }
        return total ? [...body, total] : body;
    }

    /** Click a header: ascending (descending first for figures), the other
     * way, then back to the answer's own order. */
    sortTable(turnIndex, blockIndex, block, column) {
        const key = this.tableKey(turnIndex, blockIndex);
        const current = this.state.sort[key];
        const first = this.numericColumn(block, column) ? "desc" : "asc";
        if (!current || current.column !== column) {
            this.state.sort[key] = { column, direction: first };
        } else if (current.direction === first) {
            this.state.sort[key] = { column, direction: first === "asc" ? "desc" : "asc" };
        } else {
            delete this.state.sort[key];
        }
    }

    sortIcon(turnIndex, blockIndex, column) {
        const sort = this.state.sort[this.tableKey(turnIndex, blockIndex)];
        if (!sort || sort.column !== column) {
            return "fa-sort o_odin_ai_sort_idle";
        }
        return sort.direction === "asc" ? "fa-sort-asc" : "fa-sort-desc";
    }

    ariaSort(turnIndex, blockIndex, column) {
        const sort = this.state.sort[this.tableKey(turnIndex, blockIndex)];
        if (!sort || sort.column !== column) {
            return "none";
        }
        return sort.direction === "asc" ? "ascending" : "descending";
    }

    tableLines(block, rows, separator, quote = (value) => value) {
        return [block.columns || [], ...rows].map((row) => row.map((cell) => quote(plainText(cell))).join(separator));
    }

    async copyTable(turnIndex, blockIndex, block) {
        const rows = this.tableRows(turnIndex, blockIndex, block);
        await this.writeClipboard(this.tableLines(block, rows, "\t").join("\n"), _t("Table copied"));
    }

    downloadCsv(turnIndex, blockIndex, block) {
        const rows = this.tableRows(turnIndex, blockIndex, block);
        const quote = (value) => `"${value.replace(/"/g, '""')}"`;
        // RFC 4180 lines; the byte order mark makes spreadsheets read UTF-8.
        const csv = "\uFEFF" + this.tableLines(block, rows, ",", quote).join("\r\n");
        const name = (this.state.thread.name || "answer").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "");
        downloadFile(csv, `${name.slice(0, 60) || "answer"}-${blockIndex + 1}.csv`, "text/csv;charset=utf-8");
    }

    async writeClipboard(text, message) {
        try {
            await browser.navigator.clipboard.writeText(text);
            this.notification.add(message, { type: "success" });
        } catch {
            this.notification.add(_t("The browser did not allow copying."), { type: "warning" });
        }
    }

    notify(error) {
        this.notification.add(error.data?.message || error.message || String(error), { type: "danger" });
    }
}

registry.category("actions").add("odin_account_ai.ask", AskAction);
