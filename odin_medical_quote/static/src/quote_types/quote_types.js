import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";

/**
 * The kinds of quote the office makes are its insurance classes (Cover
 * Quotes ▸ Products ▸ Insurance classes): Medical, Motor, and any class it
 * adds itself. Each comes back with the screen that quotes it.
 */
export function loadQuoteTypes(orm) {
    return orm.call("odin.insurance.class", "odin_quote_types", []);
}

/** The screen for ``type``, for ``leadId``'s client when there is one. */
export function quoteAction(type, leadId = false) {
    const params = { ...(type.action.params || {}) };
    if (leadId) {
        params.lead_id = leadId;
    }
    return { ...type.action, params };
}

function noProducts(type) {
    return _t("%s has no products yet: add them in Cover Quotes ▸ Products.", type.name);
}

/**
 * New quote: what is being quoted, before the form for it. From a lead
 * (Insurance quote), the form opens with that lead's client.
 */
export class QuoteNew extends Component {
    static template = "odin_medical_quote.QuoteNew";
    static props = ["*"];

    setup() {
        this.action = useService("action");
        this.orm = useService("orm");
        this.state = useState({ types: [], loaded: false });
        const params = (this.props.action && this.props.action.params) || {};
        this.leadId = params.lead_id || false;
        this.leadName = params.lead_name || "";
        this.noProducts = noProducts;
        onWillStart(async () => {
            this.state.types = await loadQuoteTypes(this.orm);
            this.state.loaded = true;
        });
    }

    open(type) {
        if (!type.available) {
            return;
        }
        this.action.doAction(quoteAction(type, this.leadId));
    }
}

registry.category("actions").add("odin_quote_new", QuoteNew);

/** The Quotes list, with one New button that asks which class. */
export class QuoteListController extends ListController {
    static template = "odin_medical_quote.QuoteListView";
    static components = { ...ListController.components, Dropdown, DropdownItem };

    setup() {
        super.setup();
        this.notification = useService("notification");
        this.quoteTypes = [];
        onWillStart(async () => {
            this.quoteTypes = await loadQuoteTypes(this.orm);
        });
    }

    newQuote(type) {
        if (!type.available) {
            this.notification.add(noProducts(type), { type: "info" });
            return;
        }
        this.actionService.doAction(quoteAction(type));
    }
}

registry.category("views").add("odin_quote_list", { ...listView, Controller: QuoteListController });
