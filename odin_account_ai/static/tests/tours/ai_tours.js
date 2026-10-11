import { registry } from "@web/core/registry";

/** Transaction Review: pick an item, accept it with the keyboard. */
registry.category("web_tour.tours").add("odin_account_ai_review_tour", {
    steps: () => [
        { trigger: ".o_odin_ai_queue_item:contains(TOUR TAXI)", run: "click" },
        { trigger: ".o_odin_ai_detail_head:contains(TOUR TAXI)" },
        { trigger: ".o_odin_ai_detail .o_odin_ai_rationale:contains(Looks like travel)" },
        // A real click focuses the page (tabindex -1); the tour's simulated one does not.
        {
            trigger: ".o_odin_ai_review",
            run() {
                this.anchor.focus();
            },
        },
        { trigger: ".o_odin_ai_review", run: "press a" },
        { trigger: ".o_odin_ai_review:not(:has(.o_odin_ai_queue_item:contains(TOUR TAXI)))" },
    ],
});

/** Explain: the "e" key on a P&L line opens the drivers and the summary. */
registry.category("web_tour.tours").add("odin_account_ai_explain_tour", {
    steps: () => [
        { trigger: ".o_odin_pnl_table tr[data-key='L:REV'] .o_odin_pnl_label", run: "click" },
        {
            trigger: ".o_odin_pnl",
            run() {
                this.anchor.focus();
            },
        },
        { trigger: ".o_odin_pnl", run: "press e" },
        { trigger: ".o_odin_ai_explain .o_odin_ai_driver" },
        { trigger: ".o_odin_ai_explain .o_odin_ai_points li:contains(Tour Customer)" },
        { trigger: ".o_odin_ai_explain .o_odin_ai_chip", run: "click" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_focus" },
    ],
});

/** Ask: a starter question, its answer, and a reference opening the P&L. */
registry.category("web_tour.tours").add("odin_account_ai_ask_tour", {
    steps: () => [
        { trigger: ".o_odin_ai_starter", run: "click" },
        // The steps fold away once the answer is in, and the mocked model
        // answers in milliseconds: read them from the folded list.
        { trigger: ".o_odin_ai_text:contains(Revenue was)" },
        { trigger: ".o_odin_ai_steps button:contains(1 step)", run: "click" },
        { trigger: ".o_odin_ai_steps li:contains(Reading the P&L)" },
        // A supplier called "Total Kenya Ltd" is a row like the others, even
        // last; sorting by amount (largest first) puts it on top.
        { trigger: ".o_odin_ai_table:has(:contains(Fuel suppliers)):not(:has(tr.o_odin_ai_total))" },
        { trigger: ".o_odin_ai_table:has(:contains(Fuel suppliers)) th:nth-child(3) .o_odin_ai_sort", run: "click" },
        { trigger: ".o_odin_ai_table:has(:contains(Fuel suppliers)) th:nth-child(3)[aria-sort=descending]" },
        {
            trigger:
                ".o_odin_ai_table:has(:contains(Fuel suppliers)) tbody tr:first-child td:first-child:contains(Total Kenya Ltd)",
        },
        // A closing total that adds up the rows above stays last and bold,
        // whatever the order.
        {
            trigger:
                ".o_odin_ai_table:has(:contains(Revenue by customer)) tbody tr:last-child.o_odin_ai_total td:first-child:contains(Total revenue)",
        },
        { trigger: ".o_odin_ai_table:has(:contains(Revenue by customer)) th:nth-child(2) .o_odin_ai_sort", run: "click" },
        { trigger: ".o_odin_ai_table:has(:contains(Revenue by customer)) th:nth-child(2)[aria-sort=descending]" },
        { trigger: ".o_odin_ai_table:has(:contains(Revenue by customer)) th:nth-child(2) .o_odin_ai_sort", run: "click" },
        { trigger: ".o_odin_ai_table:has(:contains(Revenue by customer)) th:nth-child(2)[aria-sort=ascending]" },
        {
            trigger:
                ".o_odin_ai_table:has(:contains(Revenue by customer)) tbody tr:first-child td:first-child:contains(Others)",
        },
        {
            trigger:
                ".o_odin_ai_table:has(:contains(Revenue by customer)) tbody tr:last-child.o_odin_ai_total td:first-child:contains(Total revenue)",
        },
        { trigger: ".o_odin_ai_followups button" },
        { trigger: ".o_odin_ai_text .o_odin_ai_chip", run: "click" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_focus[data-key='L:REV']" },
        { trigger: ".o_breadcrumb .breadcrumb-item a:contains(Ask)", run: "click" },
        { trigger: ".o_odin_ai_text:contains(Revenue was)" },
    ],
});

/** Ask: a question stopped while a step runs, then tried again; History
 * switched while another question is answered in the background. */
registry.category("web_tour.tours").add("odin_account_ai_ask_stop_tour", {
    steps: () => [
        { trigger: ".o_odin_ai_starter:contains(How did revenue last month)", run: "click" },
        // The mocked model takes two seconds over a tool step: Stop meanwhile.
        { trigger: ".o_odin_ai_composer button[aria-label='Stop this question']", run: "click" },
        { trigger: ".o_odin_ai_stopped:contains(Stopped before an answer was ready)" },
        { trigger: ".o_odin_ai_history_item.active .fa-stop-circle-o" },
        { trigger: ".o_odin_ai_stopped button:contains(Try again)", run: "click" },
        { trigger: ".o_odin_ai_text:contains(Revenue was)" },
        // A second question; while it is worked on, open the first one.
        { trigger: ".o_control_panel button:contains(New question)", run: "click" },
        { trigger: ".o_odin_ai_starter:contains(ten largest expenses)", run: "click" },
        { trigger: ".o_odin_ai_history_item:contains(ten largest expenses) .fa-spin" },
        { trigger: ".o_odin_ai_history_item:contains(How did revenue)", run: "click" },
        { trigger: ".o_odin_ai_history_item:contains(ten largest expenses):not(:has(.fa-spin))" },
        { trigger: ".o_odin_ai_question:contains(How did revenue)" },
        { trigger: ".o_odin_ai_thread:not(:has(.o_odin_ai_question:contains(ten largest expenses)))" },
    ],
});

/** Ask about a P&L line: key A opens Ask with a draft and the line's context. */
registry.category("web_tour.tours").add("odin_account_ai_ask_line_tour", {
    steps: () => [
        { trigger: ".o_odin_pnl_table tr[data-key='L:REV'] .o_odin_pnl_label", run: "click" },
        {
            trigger: ".o_odin_pnl",
            run() {
                this.anchor.focus();
            },
        },
        { trigger: ".o_odin_pnl", run: "press a" },
        { trigger: ".o_odin_ai_context:contains(Revenue)" },
        {
            trigger: ".o_odin_ai_composer textarea",
            run() {
                if (!this.anchor.value.includes("Revenue")) {
                    throw new Error(`The draft question is missing: "${this.anchor.value}"`);
                }
            },
        },
        { trigger: ".o_odin_ai_composer button[aria-label='Send']", run: "click" },
        { trigger: ".o_odin_ai_text:contains(Revenue was)" },
        { trigger: ".o_odin_ai_scope:contains(Based on)" },
        // The context went with the question: the next one starts without it.
        { trigger: ".o_odin_ai_composer_wrap:not(:has(.o_odin_ai_context))" },
    ],
});
