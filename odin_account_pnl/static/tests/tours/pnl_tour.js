import { registry } from "@web/core/registry";

/**
 * The one-page flow: unfold Revenue into its account, split the account by
 * partner, open a partner's items, peek at an entry beside the report, open
 * it in full and come back by the breadcrumb to the same unfolded report.
 */
registry.category("web_tour.tours").add("odin_account_pnl_tour", {
    steps: () => [
        { trigger: ".o_odin_pnl_table tr[data-key='L:REV'] .o_odin_pnl_label", run: "click" },
        { trigger: ".o_odin_pnl_table tr[data-key^='L:REV/A:'] .o_odin_pnl_label", run: "click" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_row_entry" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_focus .o_odin_pnl_by", run: "click" },
        { trigger: ".o_odin_pnl_rowmenu .dropdown-item:contains(Partner)", run: "click" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_row_breakdown:contains(Tour Customer) .o_odin_pnl_label", run: "click" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_row_entry:contains(Tour Customer)", run: "click" },
        { trigger: ".o_odin_pnl_panel .o_odin_pnl_peek h4" },
        { trigger: ".o_odin_pnl_peek_foot button:contains(Open)", run: "click" },
        { trigger: ".o_form_view .o_field_widget[name=partner_id]:contains(Tour Customer)" },
        { trigger: ".o_breadcrumb .breadcrumb-item a:contains(Profit)", run: "click" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_row_breakdown:contains(Tour Customer)" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_row_entry:contains(Tour Customer)" },
    ],
});

/**
 * Ledger and help: an account shown as its ledger gets a Balance column and
 * keeps its "Ledger" chip visible; "?" opens the keyboard shortcuts; the
 * one-time tip can be dismissed.
 */
registry.category("web_tour.tours").add("odin_account_pnl_ledger_tour", {
    steps: () => [
        { trigger: ".o_odin_pnl_table tr[data-key='L:REV'] .o_odin_pnl_label", run: "click" },
        { trigger: ".o_odin_pnl_table tr[data-key^='L:REV/A:'] .o_odin_pnl_label", run: "click" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_row_entry" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_focus .o_odin_pnl_by", run: "click" },
        { trigger: ".o_odin_pnl_rowmenu .dropdown-item:contains(Ledger)", run: "click" },
        { trigger: ".o_odin_pnl_table th:contains(Balance)" },
        { trigger: ".o_odin_pnl_table td.o_odin_pnl_running_col:contains(1,000.00)" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_split .o_odin_pnl_by:contains(Ledger)" },
        // A real click focuses the page (tabindex -1); the tour's simulated one does not.
        {
            trigger: ".o_odin_pnl",
            run() {
                this.anchor.focus();
            },
        },
        { trigger: ".o_odin_pnl", run: "press ?" },
        { trigger: ".o_odin_pnl_shortcuts:contains(Find an account)" },
        { trigger: ".o_odin_pnl_shortcuts .modal-footer button:contains(Got it)", run: "click" },
        { trigger: "body:not(:has(.o_odin_pnl_shortcuts))" },
        // B opens the focused line's split menu on its first item, the arrows
        // move in it and Enter picks one. Bootstrap's own keyboard handler
        // must leave this menu alone: its error would fail the tour.
        {
            trigger: ".o_odin_pnl",
            run() {
                this.anchor.focus();
            },
        },
        { trigger: ".o_odin_pnl", run: "press b" },
        { trigger: ".o_odin_pnl_rowmenu .dropdown-item:contains(Journal items)" },
        { trigger: ".o_odin_pnl_rowmenu", run: "press ArrowDown" },
        { trigger: ".o_odin_pnl_rowmenu", run: "press ArrowDown" },
        { trigger: ".o_odin_pnl_rowmenu", run: "press Enter" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_split .o_odin_pnl_by:contains(Partner)" },
        { trigger: ".o_odin_pnl_table:not(:has(.o_odin_pnl_running_col))" },
        // Escape closes the menu; the report keeps the focus.
        { trigger: ".o_odin_pnl", run: "press b" },
        { trigger: ".o_odin_pnl_rowmenu .dropdown-item:contains(Journal items)" },
        { trigger: ".o_odin_pnl_rowmenu", run: "press Escape" },
        { trigger: ".o_odin_pnl:not(:has(.o_odin_pnl_rowmenu))" },
        { trigger: ".o_odin_pnl_tip button", run: "click" },
        { trigger: ".o_odin_pnl_main:not(:has(.o_odin_pnl_tip))" },
    ],
});
