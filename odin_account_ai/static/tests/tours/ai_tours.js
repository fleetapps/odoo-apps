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
        { trigger: ".o_odin_ai_followups button" },
        { trigger: ".o_odin_ai_text .o_odin_ai_chip", run: "click" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_focus[data-key='L:REV']" },
        { trigger: ".o_breadcrumb .breadcrumb-item a:contains(Ask)", run: "click" },
        { trigger: ".o_odin_ai_text:contains(Revenue was)" },
    ],
});
