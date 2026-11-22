import { registry } from "@web/core/registry";

/**
 * The views and the filters: the view switch at the top right shows which
 * view is open, the buttons on the left keep one row, a filter shows it is on.
 */
registry.category("web_tour.tours").add("odin_stock_book_views_tour", {
    steps: () => [
        { trigger: ".o_sb_views button[aria-selected='true']:contains(Movements)" },
        { trigger: ".o_sb_kpi_balance .o_sb_kpi_value" },
        {
            // Odoo's tours run at 1366 px wide: room for every button and the
            // view switch on one row of the control panel.
            trigger: ".o_control_panel_main_buttons .o_sb_add_bill",
            run() {
                const buttons = document.querySelector(".o_control_panel_main_buttons");
                const add = buttons.querySelector(".btn-primary");
                if (
                    buttons.getBoundingClientRect().height >
                    add.getBoundingClientRect().height * 1.5
                ) {
                    throw new Error("The control panel buttons wrapped onto a second row");
                }
            },
        },
        { trigger: ".o_sb_seg button:contains(Received)", run: "click" },
        { trigger: ".o_sb_seg button[aria-pressed='true']:contains(Received)" },
        { trigger: ".o_sb_seg button[aria-pressed='false']:contains(All)" },
        { trigger: ".o_sb_views button:contains(Stock sheet)", run: "click" },
        { trigger: ".o_sb_views button[aria-selected='true']:contains(Stock sheet)" },
        { trigger: ".o_sb_table .o_sb_plain:contains(Tour Handwash)" },
        { trigger: ".o_sb_views button:contains(Count)", run: "click" },
        { trigger: ".o_sb_count_input" },
        { trigger: ".o_sb_views button[aria-selected='false']:contains(Movements)" },
        { trigger: ".o_sb_views button:contains(Out for recipes)", run: "click" },
        { trigger: ".o_sb_views button[aria-selected='true']:contains(Out for recipes)" },
        { trigger: ".o_sb_focus_recipes strong" },
        { trigger: ".o_sb_views button:contains(Movements)", run: "click" },
        { trigger: ".o_sb_summary .o_sb_kpi_label:contains(Missing details)" },
    ],
});
