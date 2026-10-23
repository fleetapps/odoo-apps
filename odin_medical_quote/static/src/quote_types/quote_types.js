import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";

/**
 * The kinds of quote the office makes. Each insurance line adds itself here
 * (medical below, motor from its own module) and appears in New quote and
 * in the Quotes list's New menu.
 */
export const quoteTypes = registry.category("odin_quote_types");

quoteTypes.add("medical", {
    sequence: 10,
    name: _t("Medical"),
    description: _t("Individual and family medical cover"),
    icon: "fa-heartbeat",
    action: "odin_medical_quote.action_medical_quote",
});

function sortedTypes() {
    return quoteTypes.getEntries()
        .map(([key, type]) => ({ key, ...type }))
        .sort((a, b) => a.sequence - b.sequence);
}

/** New quote: what is being quoted, before the form for it. */
export class QuoteNew extends Component {
    static template = "odin_medical_quote.QuoteNew";
    static props = ["*"];

    setup() {
        this.action = useService("action");
        this.types = sortedTypes();
    }

    open(type) {
        this.action.doAction(type.action);
    }
}

registry.category("actions").add("odin_quote_new", QuoteNew);

/** The Quotes list, with one New button that asks which kind. */
export class QuoteListController extends ListController {
    static template = "odin_medical_quote.QuoteListView";
    static components = { ...ListController.components, Dropdown, DropdownItem };

    setup() {
        super.setup();
        this.quoteTypes = sortedTypes();
    }

    newQuote(type) {
        this.actionService.doAction(type.action);
    }
}

registry.category("views").add("odin_quote_list", { ...listView, Controller: QuoteListController });
