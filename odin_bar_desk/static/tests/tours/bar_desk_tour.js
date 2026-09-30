import { registry } from "@web/core/registry";

function key(digit) {
    return {
        trigger: `.o_bar_desk_key:contains(${digit})`,
        run: "click",
    };
}

/** A bar tablet: sign in with a PIN, record a breakage, see it in Today. */
registry.category("web_tour.tours").add("odin_bar_desk_stock_out", {
    steps: () => [
        { trigger: ".o_bar_desk_person:contains(Mary)", run: "click" },
        key("1"),
        key("2"),
        key("3"),
        key("4"),
        { trigger: ".o_bar_desk_key:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_tile:contains(Stock out)", run: "click" },
        { trigger: ".o_bar_desk_tile:contains(Breakage)", run: "click" },
        { trigger: "button:contains(Add item)", run: "click" },
        { trigger: ".o_bar_desk_picker_search input", run: "edit Tusker" },
        { trigger: ".o_bar_desk_pick:contains(Tusker)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(2)", run: "click" },
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_line:contains(Tusker):contains(2 Units)" },
        { trigger: ".o_bar_desk_footer button:contains(Done)", run: "click" },
        { trigger: ".o_bar_desk_toast:contains(Breakage recorded)" },
        { trigger: ".o_bar_desk_timeline:contains(Breakage):contains(Mary)" },
    ],
});

/** A closing count: every line entered, crates and loose bottles, submitted. */
registry.category("web_tour.tours").add("odin_bar_desk_count", {
    steps: () => [
        { trigger: ".o_bar_desk_person:contains(Mary)", run: "click" },
        key("1"),
        key("2"),
        key("3"),
        key("4"),
        { trigger: ".o_bar_desk_key:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_tile:contains(Count)", run: "click" },
        { trigger: ".o_bar_desk_tile:contains(Closing count)", run: "click" },
        { trigger: ".o_bar_desk_footer button:disabled:contains(left)" },
        // Jameson: 1 full 750ml bottle, 12 tots left in the open one.
        { trigger: ".o_bar_desk_line:contains(Jameson) .o_bar_desk_line_main", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(1)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_field:contains(Open)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(1)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(2)", run: "click" },
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        // Tusker: 1 crate of 24 and 3 loose.
        { trigger: ".o_bar_desk_line:contains(Tusker) .o_bar_desk_line_main", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(1)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_field:contains(Loose)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(3)", run: "click" },
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_line:contains(Tusker):contains(27)" },
        // Gordon's has run out: an explicit zero.
        { trigger: ".o_bar_desk_line:contains(Gordon) .o_bar_desk_line_main", run: "click" },
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_footer button:not(:disabled):contains(Submit)", run: "click" },
        { trigger: ".modal button:contains(Submit)", run: "click" },
        { trigger: ".o_bar_desk_toast:contains(Count submitted)" },
    ],
});
