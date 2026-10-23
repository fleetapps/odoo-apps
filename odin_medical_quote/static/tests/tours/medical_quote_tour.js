import { registry } from "@web/core/registry";

/**
 * The example from the brief: a new prospect, principal, spouse and three
 * children, the Afya plan with outpatient and dental, saved to CRM.
 * 15,822 + 22,130 + 6,050 = 44,002, levies of 0.45% and stamp duty of 40:
 * 44,240.01, or .02 where the company rounds tax per line.
 */
registry.category("web_tour.tours").add("odin_medical_quote_tour", {
    steps: () => [
        { trigger: ".o_mq_title:contains(Add Medical Quote)" },
        { trigger: "#mq_name", run: "edit Tour Prospect" },
        { trigger: "#mq_phone", run: "edit 0711 222 333" },
        { trigger: "#mq_principal", run: "edit 35" },
        { trigger: "#mq_spouse_on", run: "check" },
        { trigger: "#mq_spouse", run: "edit 28" },
        { trigger: "#mq_children", run: "edit 3" },
        { trigger: ".o_mq_find", run: "click" },
        { trigger: ".o_mq_summary:contains(Tour Prospect):contains(3 children)" },
        {
            trigger: ".o_mq_table tr:contains(Below 70) .o_mq_core .o_mq_select",
            run: "click",
        },
        {
            trigger: ".o_mq_table tr:contains(Below 70) .o_mq_option:contains('Limit 50,000') .o_mq_select",
            run: "click",
        },
        {
            trigger: ".o_mq_table tr:contains(Below 70) .o_mq_option:contains('Limit 5,000') .o_mq_select",
            run: "click",
        },
        { trigger: ".o_mq_plan .o_mq_plan_row:contains(Dental):contains('6,050')" },
        { trigger: ".o_mq_plan .o_mq_total:contains('44,240.0')" },
        { trigger: ".o_mq_save", run: "click" },
        { trigger: ".o_mq_saved:contains(saved for Tour Prospect):contains(CRM pipeline)" },
    ],
});
