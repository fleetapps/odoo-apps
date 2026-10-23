import { registry } from "@web/core/registry";

/**
 * A family of five prices cover with doctor visits, puts the Afya plan on
 * their quote, gets it and asks to be called.
 */
registry.category("web_tour.tours").add("odin_medical_quote_online_tour", {
    steps: () => [
        { trigger: ".o_oq_tile:contains(My whole family)", run: "click" },
        { trigger: "#oq_age", run: "edit 35" },
        { trigger: "#oq_partner_age", run: "edit 28" },
        { trigger: "#oq_children", run: "edit 3" },
        { trigger: ".o_oq_next", run: "click" },
        { trigger: ".o_oq_plan:contains(Plan 1 (KES 200,000))" },
        { trigger: ".o_oq_chip:contains(Doctor visits)", run: "click" },
        { trigger: ".o_oq_plan:contains(Plan 1 (KES 200,000)):contains(Doctor visits)" },
        {
            trigger: ".o_oq_plan:contains(Plan 1 (KES 200,000)) .o_oq_choose",
            run: "click",
        },
        { trigger: ".o_oq_bar:contains(1 plan on your quote) .o_oq_get", run: "click" },
        { trigger: "#oq_name", run: "edit Tour Visitor" },
        { trigger: "#oq_phone", run: "edit 0733 999 888" },
        { trigger: ".o_oq_consent input", run: "check" },
        { trigger: ".o_oq_submit", run: "click", expectUnloadPage: true },
        { trigger: ".o_oq_title:contains(here is your quote)" },
        { trigger: "#advisor button[type=submit]", run: "click", expectUnloadPage: true },
        { trigger: "#advisor:contains(Thank you)" },
    ],
});
