import { registry } from "@web/core/registry";

/**
 * A private car worth 800,000: full cover, the excess protector added on
 * the cheapest insurer, that insurer on the quote, the quote.
 */
registry.category("web_tour.tours").add("odin_motor_quote_online_tour", {
    steps: () => [
        { trigger: ".o_oq_tile:contains(Private car)", run: "click" },
        { trigger: ".o_oq_tile:contains(Full cover)", run: "click" },
        { trigger: "#oq_value", run: "edit 800000" },
        { trigger: ".o_oq_field_hint:contains(800,000)" },
        { trigger: "#oq_year", run: "selectByIndex 6" },
        { trigger: ".o_oq_next", run: "click" },
        { trigger: ".o_oq_plan:contains(Test Definite):contains(Lowest price)" },
        {
            trigger: ".o_oq_plan:contains(Test Definite) .o_oq_extra_toggle:contains(Excess protector)",
            run: "click",
        },
        { trigger: ".o_oq_plan:contains(Test Definite) .o_oq_extra_toggle.o_on" },
        { trigger: ".o_oq_plan:contains(Test Definite) .o_oq_choose", run: "click" },
        { trigger: ".o_oq_bar:contains(1 insurer on your quote) .o_oq_get", run: "click" },
        { trigger: "#oq_name", run: "edit Tour Driver" },
        { trigger: "#oq_phone", run: "edit 0733 222 111" },
        { trigger: "#oq_registration", run: "edit KDB 456B" },
        { trigger: ".o_oq_consent input", run: "check" },
        { trigger: ".o_oq_submit", run: "click", expectUnloadPage: true },
        { trigger: ".o_oq_title:contains(here is your quote)" },
        { trigger: ".o_oq_quote:contains(Excess protector)" },
    ],
});
