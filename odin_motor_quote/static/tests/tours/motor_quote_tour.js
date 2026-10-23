import { registry } from "@web/core/registry";

/** New ▸ Motor quote from the Quotes list, a private car worth 800,000,
 * Test Definite's price (its 37,500 minimum), saved to CRM. */
registry.category("web_tour.tours").add("odin_motor_quote_tour", {
    steps: () => [
        { trigger: ".o_quote_new_button", run: "click" },
        { trigger: ".o-dropdown-item:contains(Motor quote)", run: "click" },
        { trigger: ".o_mq_title:contains(Add Motor Quote)" },
        { trigger: "#mq_name", run: "edit Tour Motorist" },
        { trigger: "#mq_phone", run: "edit 0711 333 444" },
        { trigger: "#mq_class", run: "selectByLabel Private car" },
        { trigger: "#mq_value", run: "edit 800000" },
        { trigger: ".o_mq_find", run: "click" },
        { trigger: ".o_mq_table tr:contains(Test Definite) .o_mq_core .o_mq_select", run: "click" },
        { trigger: ".o_mq_plan:contains(Test Definite) .o_mq_total:contains('37,')" },
        { trigger: ".o_mq_save", run: "click" },
        { trigger: ".o_mq_saved:contains(saved for Tour Motorist)" },
    ],
});
