import { registry } from "@web/core/registry";

/**
 * New quote ▸ Accident, a class the office added: a new prospect, a sum
 * insured of 1,000,000 for three people, Shield's product, saved.
 * 3 × 0.4% of 1,000,000 = 12,000, levies of 0.45% and stamp duty of 40.
 */
registry.category("web_tour.tours").add("odin_general_quote_tour", {
    steps: () => [
        { trigger: ".o_quote_new_type[data-class=accident]:contains(Personal accident cover)", run: "click" },
        { trigger: ".o_mq_title:contains(Add Accident Quote)" },
        { trigger: "#mq_name", run: "edit Tour Accident Client" },
        { trigger: "#mq_phone", run: "edit 0711 222 444" },
        { trigger: "#mq_amount", run: "edit 1000000" },
        { trigger: "#mq_people", run: "edit 3" },
        { trigger: "#mq_details", run: "edit Teachers" },
        { trigger: ".o_mq_find", run: "click" },
        { trigger: ".o_mq_summary:contains(Tour Accident Client):contains(3 people)" },
        { trigger: ".o_mq_unavailable:contains(1 insurer cannot quote these details)" },
        {
            trigger: ".o_mq_table tr:contains(Test Shield) .o_mq_core .o_mq_select",
            run: "click",
        },
        { trigger: ".o_mq_plan .o_mq_plan_row:contains(Basic premium):contains('12,000')" },
        { trigger: ".o_mq_plan .o_mq_total:contains('12,094')" },
        { trigger: ".o_mq_save", run: "click" },
        { trigger: ".o_mq_saved:contains(Tour Accident Client)" },
    ],
});
