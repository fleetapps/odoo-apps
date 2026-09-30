import { registry } from "@web/core/registry";

function key(digit) {
    return {
        trigger: `.o_bar_desk_key:contains(${digit})`,
        run: "click",
    };
}

const signIn = [
    { trigger: ".o_bar_desk_person:contains(Mary)", run: "click" },
    key("1"),
    key("2"),
    key("3"),
    key("4"),
    { trigger: ".o_bar_desk_key:contains(OK)", run: "click" },
    { trigger: ".o_bar_desk_step:contains(Moves)" },
];

/** Log a move from the paper sheet: two crates of Tusker, store to Bulls Eye. */
registry.category("web_tour.tours").add("odin_bar_desk_move", {
    steps: () => [
        ...signIn,
        { trigger: "button:contains(Log a move)", run: "click" },
        { trigger: ".o_bar_desk_grid:eq(0) button:contains(Main Store)", run: "click" },
        { trigger: ".o_bar_desk_grid:eq(1) button:contains(Bulls Eye)", run: "click" },
        { trigger: "button:contains(Add item)", run: "click" },
        { trigger: ".o_bar_desk_picker_search input", run: "edit Tusker" },
        { trigger: ".o_bar_desk_pick:contains(Tusker)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(2)", run: "click" },
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_line:contains(Tusker):contains(2 × Crate)" },
        { trigger: ".o_bar_desk_footer button:contains(Save move)", run: "click" },
        { trigger: ".o_bar_desk_toast:contains(Moved from Main Store to Bulls Eye)" },
        { trigger: ".o_bar_desk_moves:contains(Main Store):contains(Bulls Eye):contains(Mary)" },
    ],
});

/** The morning count of Bulls Eye against the expected stock, then a reason
 * for each difference. */
registry.category("web_tour.tours").add("odin_bar_desk_count", {
    steps: () => [
        ...signIn,
        { trigger: ".o_bar_desk_location:contains(BE)", run: "click" },
        { trigger: ".o_bar_desk_line:contains(Jameson):contains(Expected 50 tots)" },
        { trigger: ".o_bar_desk_footer button:disabled:contains(left)" },
        // Jameson: 1 full 750ml bottle, 12 tots left in the open one.
        { trigger: ".o_bar_desk_line:contains(Jameson) .o_bar_desk_line_main", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(1)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_field:contains(Open)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(1)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(2)", run: "click" },
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_line:contains(Jameson):contains(−13 tots)" },
        // Tusker: a crate of 24, and loose bottles in two fridges: 1 + 2.
        { trigger: ".o_bar_desk_line:contains(Tusker) .o_bar_desk_line_main", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(1)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_field:contains(Loose)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(1)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(+)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(2)", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_field:contains(= 3)" },
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_line:contains(Tusker):contains(27)" },
        // Gordon's: none expected, none there.
        { trigger: ".o_bar_desk_line:contains(Gordon) .o_bar_desk_same", run: "click" },
        { trigger: ".o_bar_desk_footer button:not(:disabled):contains(Finish count)", run: "click" },
        { trigger: ".modal button:contains(Finish count)", run: "click" },
        { trigger: ".o_bar_desk_toast:contains(2 to explain)" },
        { trigger: ".o_bar_desk_diff:contains(Jameson) .o_bar_desk_diff_main", run: "click" },
        { trigger: ".o_bar_desk_diff:contains(Jameson) .o_bar_desk_reason:contains(Spillage)", run: "click" },
        { trigger: ".o_bar_desk_diff:contains(Jameson):contains(Spillage)" },
        { trigger: ".o_bar_desk_diff:contains(Tusker) .o_bar_desk_diff_main", run: "click" },
        { trigger: ".o_bar_desk_diff:contains(Tusker) .o_bar_desk_reason:contains(Unexplained)", run: "click" },
        { trigger: ".o_bar_desk_footer button:contains(Done)", run: "click" },
        { trigger: ".o_bar_desk_location:contains(BE):contains(2 explained)" },
    ],
});
