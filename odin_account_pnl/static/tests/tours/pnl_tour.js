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

/**
 * The Views button names the view on screen: the default view the page opens
 * on, then one picked from the menu. Schedule Send then opens that view. A
 * change marks the view; Revert takes it back; with no view, Schedule Send
 * opens a new schedule of the page as it is.
 */
registry.category("web_tour.tours").add("odin_account_pnl_views_tour", {
    steps: () => [
        { trigger: ".o_odin_pnl_table" },
        { trigger: ".o_control_panel button:has(.fa-star):contains(Opening view)", run: "click" },
        { trigger: ".o_odin_pnl_views_menu .dropdown-item:contains(Board pack)", run: "click" },
        { trigger: ".o_control_panel button:has(.fa-star):contains(Board pack)" },
        { trigger: ".o_odin_pnl_schedule", run: "click" },
        {
            trigger: ".modal .o_form_view .o_field_widget[name=name] input",
            run() {
                if (this.anchor.value !== "Board pack") {
                    throw new Error(`Schedule Send opened "${this.anchor.value}", not the view on screen`);
                }
            },
        },
        { trigger: ".modal .btn-close", run: "click" },
        { trigger: "body:not(:has(.modal))" },
        // A change to the page marks the view, and Revert takes it back.
        { trigger: ".o_odin_pnl_views_button:not(:has(.o_odin_pnl_view_changed))" },
        { trigger: ".o_odin_pnl_detail", run: "click" },
        { trigger: ".o_odin_pnl_menu .dropdown-item:contains(Every transaction)", run: "click" },
        { trigger: ".o_odin_pnl_views_button .o_odin_pnl_view_changed" },
        { trigger: ".o_odin_pnl_views_button", run: "click" },
        { trigger: ".o_odin_pnl_views_menu .dropdown-item:contains(Revert to the view as saved)", run: "click" },
        { trigger: ".o_odin_pnl_views_button:contains(Board pack):not(:has(.o_odin_pnl_view_changed))" },
        // Back to the P&L with no view, then schedule the page as it is.
        { trigger: ".o_odin_pnl_views_button", run: "click" },
        { trigger: ".o_odin_pnl_views_menu .dropdown-item:contains(Back to the default)", run: "click" },
        { trigger: ".o_odin_pnl_views_button:contains(Views)" },
        { trigger: ".o_odin_pnl_schedule", run: "click" },
        // Scheduled monthly from the start: the day of the month is asked for.
        { trigger: ".modal .o_form_view .o_field_widget[name=send_day]" },
        {
            trigger: ".modal .o_form_view .o_field_widget[name=name] input",
            run() {
                if (this.anchor.value !== "Profit & Loss") {
                    throw new Error(`A new schedule was named "${this.anchor.value}"`);
                }
            },
        },
    ],
});

/**
 * Flux review: compare with the previous period from the table's call to
 * action, filter the material changes, explain one in place, raise the
 * materiality until nothing is material, and go to a headline figure's line.
 */
registry.category("web_tour.tours").add("odin_account_pnl_flux_tour", {
    steps: () => [
        { trigger: ".o_odin_pnl_kpi:contains(Revenue)" },
        { trigger: ".o_odin_pnl_tab_cta", run: "click" },
        { trigger: ".o_odin_pnl_table th.o_odin_pnl_flux_col:contains(Variance)" },
        { trigger: ".o_odin_pnl_kpi:contains(Revenue) .o_odin_pnl_kpi_change.o_odin_pnl_good" },
        { trigger: ".o_odin_pnl_tab[data-quick=material]", run: "click" },
        { trigger: ".o_odin_pnl_detail:contains(Accounts)" },
        {
            trigger: ".o_odin_pnl_table tr.o_odin_pnl_material[data-key^='L:REV/A:'] .o_odin_pnl_explain_add",
            run: "click",
        },
        { trigger: ".o_odin_pnl_panel textarea", run: "edit A new client this month" },
        { trigger: ".o_odin_pnl_panel .btn-primary:contains(Add note)", run: "click" },
        { trigger: ".o_odin_pnl_panel .o_odin_pnl_note:contains(A new client)" },
        { trigger: ".o_odin_pnl_panel button[aria-label=Close]", run: "click" },
        { trigger: ".o_odin_pnl_table tr[data-key^='L:REV/A:'] .o_odin_pnl_explain:contains(A new client)" },
        // Above the change, nothing is material any more.
        { trigger: ".o_odin_pnl_materiality_btn", run: "click" },
        { trigger: "#odin_pnl_material_amount", run: "edit 5000" },
        { trigger: ".o_odin_pnl_materiality_form .btn-primary:contains(Apply)", run: "click" },
        { trigger: ".o_odin_pnl_materiality_value:contains(5,000)" },
        { trigger: ".o_odin_pnl_empty:contains(No material change)" },
        { trigger: ".o_odin_pnl_empty button:contains(Show all lines)", run: "click" },
        { trigger: ".o_odin_pnl_tab.active[data-quick=all]" },
        { trigger: ".o_odin_pnl_kpi:contains(Net Profit)", run: "click" },
        { trigger: ".o_odin_pnl_table tr.o_odin_pnl_focus[data-key='L:NET']" },
        // The × beside Compare takes the comparison off again.
        { trigger: ".o_odin_pnl_clear_comparison", run: "click" },
        { trigger: ".o_odin_pnl_table:not(:has(th.o_odin_pnl_flux_col))" },
        { trigger: ".o_odin_pnl_comparison:contains(None)" },
        // Filters: a journal picked in the panel shows at once, in the panel
        // and as a chip under the bar, and the chip's × takes it off.
        { trigger: ".o_odin_pnl_filter_btn", run: "click" },
        { trigger: ".o_odin_pnl_fp_pill:first", run: "click" },
        { trigger: ".o_odin_pnl_fp_pill.active" },
        { trigger: ".o_odin_pnl_active_chip[data-filter=journals]" },
        { trigger: ".o_odin_pnl_fp_pill.active", run: "click" },
        { trigger: ".o_odin_pnl_fp:not(:has(.o_odin_pnl_fp_pill.active))" },
        { trigger: ".o_odin_pnl_fp_pill:first", run: "click" },
        { trigger: ".o_odin_pnl_active_chip[data-filter=journals] button", run: "click" },
        { trigger: ".o_odin_pnl_filters:not(:has(.o_odin_pnl_active_chip))" },
    ],
});

/**
 * Schedule: the dialog opens on a monthly schedule of the page, a test goes
 * to oneself and shows in the history without closing the dialog, and the
 * saved schedule is the view on screen, its button saying so.
 */
registry.category("web_tour.tours").add("odin_account_pnl_schedule_tour", {
    steps: () => [
        { trigger: ".o_odin_pnl_table" },
        { trigger: ".o_odin_pnl_schedule:contains(Schedule)", run: "click" },
        { trigger: ".modal .o_odin_pnl_schedule_summary:contains(Every month on day 2)" },
        // The frequency is a segmented control: its labels are what is clicked.
        { trigger: ".modal .o_field_widget[name=schedule] input[data-value=weekly] + label", run: "click" },
        { trigger: ".modal .o_odin_pnl_schedule_summary:contains(Every Monday)" },
        // Weekly with Last month: the dialog says the figures would repeat.
        { trigger: ".modal .o_odin_pnl_sf_callout:contains(stays the same)" },
        { trigger: ".modal .o_field_widget[name=period_preset] .o_select_menu_toggler", run: "click" },
        { trigger: ".o_select_menu_item:contains(This month)", run: "click" },
        { trigger: '.modal .o_field_widget[name=period_preset] input:value("This month")' },
        { trigger: ".modal:not(:has(.o_odin_pnl_sf_callout:contains(stays the same)))" },
        { trigger: ".modal .o_field_widget[name=recipient_emails] input", run: "edit board@example.com" },
        { trigger: ".modal .o_field_widget[name=subject] input", run: "edit Results for {period}" },
        // The email as it would go, over the dialog, which stays behind it.
        { trigger: ".modal .o_odin_pnl_preview_btn", run: "click" },
        { trigger: ".o_odin_pnl_preview_dialog .o_odin_pnl_mail_subject:contains(Results for)" },
        { trigger: ".o_odin_pnl_preview_dialog .o_odin_pnl_mail_head:contains(board@example.com)" },
        { trigger: ".o_odin_pnl_preview_dialog .modal-footer button:contains(Close)", run: "click" },
        { trigger: "body:not(:has(.o_odin_pnl_preview_dialog))" },
        { trigger: ".modal .o_odin_pnl_schedule_form" },
        { trigger: ".modal button[name=action_send_test]", run: "click" },
        { trigger: ".o_notification:contains(Test:)" },
        { trigger: ".modal .o_field_widget[name=log_ids] .o_data_row:contains(Test)" },
        { trigger: ".modal .modal-footer button.btn-primary:contains(Save)", run: "click" },
        { trigger: "body:not(:has(.modal))" },
        { trigger: ".o_odin_pnl_schedule:contains(Scheduled)" },
        { trigger: ".o_odin_pnl_views_button:contains(Profit & Loss)" },
    ],
});
