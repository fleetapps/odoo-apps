import { Component, onMounted, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { useSetupAction } from "@web/search/action_hook";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const HANDLE = /\[(E\d+)\]/g;
const NUMERIC = /^[-+(]?[\d\s.,]+%?\)?$/;

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

/**
 * Ask the Ledger: questions about the books, answered from them.
 *
 * The page drives the conversation one step at a time (ask_step), so each
 * request stays short and the steps show as they happen ("Reading the P&L
 * for Sep 2026"). Every figure in an answer cites the tool rows it came
 * from; a chip opens that row in the P&L or the document itself.
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
        this.state = useState({
            boot: null,
            thread: null,
            input: "",
            sending: false,
            history: restored?.history ?? !this.env.isSmall,
            openSteps: {},
        });
        this.alive = true;
        // Follow the newest answer only while the reader is already at the
        // bottom. A long answer arrives over several progress ticks, and each
        // one re-runs the scroll effect: without this, scrolling up to re-read
        // something snaps straight back down.
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
            const params = this.props.action.params || {};
            if (!restored && params.question) {
                this.ask(params.question, params.context);
            } else {
                this.inputRef.el?.focus();
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
            () => [this.state.thread?.turns?.length, this.lastTurn?.progress?.length, this.state.thread?.state]
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

    /** The server caps a question at max_steps and says where it has got to;
     * "Thinking…" alone leaves the reader unable to tell progress from a hang. */
    get progressLabel() {
        const thread = this.state.thread;
        if (!thread?.step || !thread?.max_steps) {
            return _t("Thinking…");
        }
        return _t("Thinking… step %(step)s of %(max)s", {
            step: thread.step,
            max: thread.max_steps,
        });
    }

    /** "E3" is a handle, not something to read mid-sentence: show the number. */
    refNumber(ref) {
        return String(ref || "").replace(/^E/, "");
    }

    get canAsk() {
        return !this.state.boot?.ready && !this.running && !this.state.sending && this.state.input.trim();
    }

    // ------------------------------------------------------------------
    // Asking
    // ------------------------------------------------------------------

    async ask(question, context = null) {
        question = (question || "").trim();
        if (!question || this.running || this.state.sending) {
            return;
        }
        this.state.sending = true;
        try {
            const thread = this.state.thread;
            if (thread && thread.state === "done" && !context) {
                this.state.thread = await this.orm.call("odin.ai.conversation", "ask_followup", [[thread.id], question]);
            } else {
                const id = await this.orm.call("odin.ai.conversation", "ask_start", [question, context]);
                this.state.thread = await this.orm.call("odin.ai.conversation", "get_thread", [[id]]);
                this.state.boot.recent.unshift({ id, name: question, state: "running" });
            }
            this.state.input = "";
        } catch (error) {
            this.notify(error);
            return;
        } finally {
            this.state.sending = false;
        }
        await this.run();
    }

    /** Step until answered. A "busy" reply means another request holds the
     * conversation (a double click): wait and look again. */
    async run() {
        if (this.looping) {
            return;
        }
        this.looping = true;
        try {
            while (this.alive && this.state.thread?.state === "running") {
                const thread = await this.orm.call("odin.ai.conversation", "ask_step", [[this.state.thread.id]]);
                if (!this.alive) {
                    return;
                }
                this.state.thread = thread;
                if (thread.busy) {
                    await new Promise((resolve) => browser.setTimeout(resolve, 800));
                }
            }
        } catch (error) {
            this.notify(error);
            if (this.state.thread) {
                this.state.thread = await this.orm.call("odin.ai.conversation", "get_thread", [[this.state.thread.id]]);
            }
        } finally {
            this.looping = false;
            const recent = this.state.boot.recent.find((item) => item.id === this.state.thread?.id);
            if (recent) {
                recent.state = this.state.thread.state;
            }
            this.inputRef.el?.focus();
        }
    }

    async retry() {
        try {
            this.state.thread = await this.orm.call("odin.ai.conversation", "ask_retry", [[this.state.thread.id]]);
        } catch (error) {
            this.notify(error);
            return;
        }
        await this.run();
    }

    async open(id) {
        try {
            this.state.thread = await this.orm.call("odin.ai.conversation", "get_thread", [[id]]);
        } catch (error) {
            this.notify(error);
            return;
        }
        if (this.env.isSmall) {
            this.state.history = false;
        }
        if (this.running) {
            this.run();
        }
    }

    newConversation() {
        this.state.thread = null;
        this.state.input = "";
        this.inputRef.el?.focus();
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
            ev.preventDefault();
            this.ask(this.state.input);
        }
    }

    // ------------------------------------------------------------------
    // Answers
    // ------------------------------------------------------------------

    parts(text) {
        return splitText(text, this.state.thread?.evidence || {});
    }

    /** Text without its references, for a KPI figure. */
    plain(text) {
        return (text || "").replace(HANDLE, "").replace(/\*\*/g, "").trim();
    }

    label(ref) {
        return this.state.thread?.evidence?.[ref] || ref;
    }

    isNumeric(cell) {
        return NUMERIC.test((cell || "").replace(HANDLE, "").trim());
    }

    /** A table column whose cells are all figures is right-aligned, header included. */
    numericColumn(block, index) {
        const rows = block.rows || [];
        return rows.length > 0 && rows.every((row) => !row[index] || this.isNumeric(row[index]));
    }

    /** References of a block not already shown inline in its text. */
    extraRefs(block) {
        const inline = new Set([...(block.text || "").matchAll(HANDLE)].map((match) => match[1]));
        return [...new Set(block.evidence || [])].filter((ref) => !inline.has(ref));
    }

    async openEvidence(ref) {
        try {
            const action = await this.orm.call("odin.ai.conversation", "open_evidence", [[this.state.thread.id], ref]);
            await this.action.doAction(action);
        } catch (error) {
            this.notify(error);
        }
    }

    toggleSteps(index) {
        this.state.openSteps[index] = !this.state.openSteps[index];
    }

    async feedback(value) {
        const thread = this.state.thread;
        const next = thread.feedback === value ? false : value;
        await this.orm.call("odin.ai.conversation", "set_feedback", [[thread.id], next]);
        thread.feedback = next;
    }

    async copy(turn) {
        const plain = (text) => (text || "").replace(HANDLE, "").replace(/\*\*/g, "").replace(/\s+([.,;:])/g, "$1");
        const lines = [];
        for (const block of turn.answer.blocks) {
            if (block.type === "kpi") {
                lines.push(`${plain(block.text)}: ${plain(block.value)}`);
            } else if (block.type === "bullets") {
                lines.push(...(block.items || []).map((item) => `• ${plain(item)}`));
            } else if (block.type === "table") {
                lines.push((block.columns || []).join("\t"));
                lines.push(...(block.rows || []).map((row) => row.map(plain).join("\t")));
            } else {
                lines.push(plain(block.text));
            }
            lines.push("");
        }
        try {
            await browser.navigator.clipboard.writeText(lines.join("\n").trim());
            this.notification.add(_t("Answer copied"), { type: "success" });
        } catch {
            this.notification.add(_t("The browser did not allow copying."), { type: "warning" });
        }
    }

    notify(error) {
        this.notification.add(error.data?.message || error.message || String(error), { type: "danger" });
    }
}

registry.category("actions").add("odin_account_ai.ask", AskAction);
