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

/** A supplier delivery from the invoice in hand: a price a zero too high is
 * caught before it is kept, a product never stocked is added on the spot,
 * and this supplier's invoice, unusually, shows no VAT. Marked paid from
 * M-Pesa there and then. */
registry.category("web_tour.tours").add("odin_bar_desk_supplier_delivery", {
    steps: () => [
        ...signIn,
        { trigger: ".o_bar_desk_tab:contains(Supplier delivery)", run: "click" },
        { trigger: ".o_bar_desk_card:contains(Tony West)", run: "click" },
        { trigger: "button:contains(Add item)", run: "click" },
        { trigger: ".o_bar_desk_picker_search input", run: "edit Tusker" },
        { trigger: ".o_bar_desk_pick:contains(Tusker Lager)", run: "click" },
        // Opens on what Tony West charges for a crate, VAT included as on
        // his invoice (2,400 + 16%): only the quantity to type.
        { trigger: ".o_bar_desk_keypad .o_bar_desk_field:contains(Price per Crate of 24):contains(2784)" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(5)", run: "click" },
        { trigger: ".o_bar_desk_keypad:contains(Line total 13,920)" },
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_line:contains(Tusker):contains(5 × Crate of 24):contains(2,784/Crate of 24)" },
        // The price on the invoice is 2,500; a zero too many is questioned.
        { trigger: ".o_bar_desk_line:contains(Tusker) .o_bar_desk_line_main", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_field:contains(Price)", run: "click" },
        key("2"),
        key("5"),
        key("0"),
        key("0"),
        key("0"),
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_keypad .alert:contains(9 times that)" },
        { trigger: ".o_bar_desk_keypad button:contains(Yes, that is right)" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key .fa-long-arrow-left", run: "click" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_field:contains(Price):contains(2500)" },
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_line:contains(Tusker):contains(2,500/Crate of 24)" },
        // Never stocked before: added from the invoice.
        { trigger: "button:contains(Add item)", run: "click" },
        { trigger: ".o_bar_desk_picker_search input", run: "edit Tusker Cider 500ml" },
        { trigger: ".o_bar_desk_pick:contains(New product)", run: "click" },
        { trigger: ".o_bar_desk_sheet .o_bar_desk_chip:contains(Beer, RTD & Cider)", run: "click" },
        { trigger: ".o_bar_desk_sheet .o_bar_desk_chip.btn-primary:contains(Units · Crate of 24)" },
        { trigger: ".o_bar_desk_sheet button:contains(Next: quantity and price)", run: "click" },
        { trigger: ".o_bar_desk_sheet .alert:contains(Type what it sells for, per unit)" },
        { trigger: "#o_bar_desk_new_sale", run: "edit 300" },
        { trigger: ".o_bar_desk_sheet button:contains(Next: quantity and price)", run: "click" },
        { trigger: ".o_bar_desk_keypad:contains(Tusker Cider 500ml)" },
        { trigger: ".o_bar_desk_keypad .o_bar_desk_key:contains(2)", run: "click" },
        // No price yet: OK asks for it rather than keeping a free line.
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_keypad .alert:contains(Type the price per Crate of 24)" },
        key("4"),
        key("8"),
        key("0"),
        key("0"),
        { trigger: ".o_bar_desk_keypad button:contains(OK)", run: "click" },
        { trigger: ".o_bar_desk_line:contains(Tusker Cider):contains(New)" },
        // Prices include VAT unless the invoice says otherwise; this one has none.
        { trigger: ".o_bar_desk_body:contains(Prices include VAT)" },
        { trigger: ".o_bar_desk_body button:contains(Change)", run: "click" },
        { trigger: "button:contains(No VAT)", run: "click" },
        { trigger: ".o_bar_desk_card:contains(Invoice total):contains(22,100):contains(No VAT on this invoice)" },
        { trigger: ".o_bar_desk_footer button:contains(Next: check what arrived)", run: "click" },
        { trigger: ".o_bar_desk_footer button:contains(Next)", run: "click" },
        { trigger: ".o_bar_desk_card:contains(Invoice total):contains(22,100)" },
        { trigger: "button:contains(Mark as credit purchase)" },
        { trigger: "button:contains(Mark as paid)", run: "click" },
        { trigger: ".o_bar_desk_toast:contains(In the store and marked paid)" },
    ],
});
