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
